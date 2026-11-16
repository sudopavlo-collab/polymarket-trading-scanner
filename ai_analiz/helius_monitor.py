"""
Helius API integration - enriched transaction parsing
"""
import aiohttp
from typing import List, Dict, Set
from config import HELIUS_API, HELIUS_API_KEY, WATCH_WALLETS
import logging

logger = logging.getLogger(__name__)


class HeliusMonitor:
    """Мониторинг транзакций через Helius Enhanced API"""
    
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.seen_signatures: Dict[str, Set[str]] = {w: set() for w in WATCH_WALLETS}
        self.processed_tokens: Set[str] = set()
    
    async def get_parsed_transactions(self, wallet: str, limit: int = 10) -> List[dict]:
        """
        Получить распарсенные транзакции от Helius
        
        Returns:
            List[dict]: Список enriched транзакций
        """
        url = f"{HELIUS_API}/addresses/{wallet}/transactions"
        params = {
            "api-key": HELIUS_API_KEY,
            "limit": limit,
        }
        
        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.error(f"Helius API error: {resp.status}")
                    return []
                
                transactions = await resp.json()
                return transactions
        
        except Exception as e:
            logger.error(f"Error getting Helius transactions for {wallet[:8]}: {e}")
            return []
    
    def extract_swaps_from_transaction(self, tx: dict) -> List[dict]:
        """
        Извлечь информацию о свопах из enriched транзакции Helius
        
        Returns:
            List[dict]: [{
                'type': 'buy' or 'sell',
                'token_mint': str,
                'token_amount': float,
                'sol_amount': float,
                'signature': str
            }]
        """
        swaps = []
        
        try:
            signature = tx.get("signature")
            
            # Helius предоставляет tokenTransfers
            token_transfers = tx.get("tokenTransfers", [])
            
            # Ищем свопы: когда SOL уходит и приходит токен (или наоборот)
            sol_change = 0
            token_changes = {}
            
            # Анализируем нативные трансферы (SOL)
            native_transfers = tx.get("nativeTransfers", [])
            for transfer in native_transfers:
                amount = transfer.get("amount", 0) / 1_000_000_000  # lamports to SOL
                from_addr = transfer.get("fromUserAccount")
                to_addr = transfer.get("toUserAccount")
                
                # Если кошелёк отправил SOL - это потенциальная покупка
                if from_addr in WATCH_WALLETS:
                    sol_change -= amount
                # Если кошелёк получил SOL - это потенциальная продажа
                elif to_addr in WATCH_WALLETS:
                    sol_change += amount
            
            # Анализируем токен трансферы
            for transfer in token_transfers:
                mint = transfer.get("mint")
                amount = transfer.get("tokenAmount", 0)
                from_addr = transfer.get("fromUserAccount")
                to_addr = transfer.get("toUserAccount")
                
                if not mint:
                    continue
                
                # Если кошелёк получил токен
                if to_addr in WATCH_WALLETS:
                    if mint not in token_changes:
                        token_changes[mint] = 0
                    token_changes[mint] += amount
                
                # Если кошелёк отправил токен
                if from_addr in WATCH_WALLETS:
                    if mint not in token_changes:
                        token_changes[mint] = 0
                    token_changes[mint] -= amount
            
            # Определяем тип свопа
            for mint, token_change in token_changes.items():
                # Токен увеличился + SOL уменьшился = ПОКУПКА
                if token_change > 0 and sol_change < 0:
                    swaps.append({
                        'type': 'buy',
                        'token_mint': mint,
                        'token_amount': token_change,
                        'sol_amount': abs(sol_change),
                        'signature': signature
                    })
                    logger.info(f"  📈 BUY detected: {mint} | Amount: {token_change:,.0f} | SOL: {abs(sol_change):.4f}")
                
                # Токен уменьшился + SOL увеличился = ПРОДАЖА
                elif token_change < 0 and sol_change > 0:
                    swaps.append({
                        'type': 'sell',
                        'token_mint': mint,
                        'token_amount': abs(token_change),
                        'sol_amount': sol_change,
                        'signature': signature
                    })
        
        except Exception as e:
            logger.debug(f"Error extracting swaps: {e}")
        
        return swaps
    
    async def scan_new_transactions(self, wallet: str, limit: int = 10) -> List[str]:
        """
        Сканировать новые транзакции и вернуть купленные токены
        
        Returns:
            List[str]: Список mint адресов токенов которые были куплены
        """
        new_tokens = []
        
        # Получаем enriched транзакции
        transactions = await self.get_parsed_transactions(wallet, limit)
        
        for tx in transactions:
            sig = tx.get("signature")
            if not sig or sig in self.seen_signatures[wallet]:
                continue
            
            # Помечаем как обработанную
            self.seen_signatures[wallet].add(sig)
            
            # Извлекаем свопы
            swaps = self.extract_swaps_from_transaction(tx)
            
            for swap in swaps:
                if swap['type'] == 'buy':
                    mint = swap['token_mint']
                    
                    # Добавляем только если ещё не видели
                    if mint not in self.processed_tokens:
                        new_tokens.append(mint)
                        self.processed_tokens.add(mint)
                        logger.info(f"🔔 NEW TOKEN from {wallet[:8]}: {mint}")
        
        return new_tokens
    
    async def scan_all_wallets(self, limit: int = 10) -> List[tuple]:
        """
        Сканировать все кошельки
        
        Returns:
            List[tuple]: [(wallet, mint), ...]
        """
        import asyncio
        
        all_new_tokens = []
        
        for wallet in WATCH_WALLETS:
            try:
                new_mints = await self.scan_new_transactions(wallet, limit)
                for mint in new_mints:
                    all_new_tokens.append((wallet, mint))
                
                # Задержка между запросами чтобы не превысить rate limit
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error scanning wallet {wallet[:8]}: {e}")
        
        return all_new_tokens

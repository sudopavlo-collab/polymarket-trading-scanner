"""
Solana blockchain monitoring - отслеживание транзакций кошельков
"""
import asyncio
import aiohttp
from typing import List, Dict, Set
from config import SOLANA_RPC, WATCH_WALLETS
import logging

logger = logging.getLogger(__name__)


class SolanaMonitor:
    """Мониторинг транзакций Solana кошельков"""
    
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.seen_signatures: Dict[str, Set[str]] = {w: set() for w in WATCH_WALLETS}
        self.processed_tokens: Set[str] = set()  # Все токены которые мы уже видели
    
    async def get_recent_signatures(self, wallet: str, limit: int = 10) -> List[dict]:
        """Получить последние транзакции кошелька"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": limit}],
        }
        try:
            async with self.session.post(SOLANA_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                res = await resp.json()
                return res.get("result", [])
        except Exception as e:
            logger.error(f"Error getting signatures for {wallet[:8]}: {e}")
            return []
    
    async def get_transaction(self, signature: str) -> dict:
        """Получить детали транзакции"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0
                }
            ],
        }
        try:
            async with self.session.post(SOLANA_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                res = await resp.json()
                return res.get("result")
        except Exception as e:
            logger.debug(f"Error getting transaction {signature[:8]}: {e}")
            return None
    
    def extract_bought_tokens(self, tx_data: dict) -> List[str]:
        """Извлечь токены которые были КУПЛЕНЫ в транзакции"""
        if not tx_data:
            return []
        
        bought_mints = []
        
        try:
            meta = tx_data.get("meta", {})
            
            # Сравниваем балансы ДО и ПОСЛЕ транзакции
            pre_balances = meta.get("preTokenBalances", [])
            post_balances = meta.get("postTokenBalances", [])
            
            # Создаём словарь балансов ДО с owner
            pre_data = {}
            for balance in pre_balances:
                mint = balance.get("mint")
                owner = balance.get("owner")
                amount = int(balance.get("uiTokenAmount", {}).get("amount", 0))
                key = (mint, owner)
                pre_data[key] = amount
            
            # Проверяем что увеличилось ПОСЛЕ
            for balance in post_balances:
                mint = balance.get("mint")
                owner = balance.get("owner")
                post_amount = int(balance.get("uiTokenAmount", {}).get("amount", 0))
                key = (mint, owner)
                pre_amount = pre_data.get(key, 0)
                
                # Если баланс токена ВЫРОС значительно (не пыль) - значит его купили
                if post_amount > pre_amount and mint and post_amount > 1000:  # Минимум 1000 atomic units
                    # Исключаем известные стейблкоины и wrapped токены
                    if mint not in {
                        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
                        "So11111111111111111111111111111111111111112",   # wSOL
                    }:
                        bought_mints.append(mint)
                        logger.info(f"  ✓ Token BOUGHT: {mint} | Amount: {post_amount:,} | Owner: {owner[:8]}...")
        
        except Exception as e:
            logger.error(f"Error extracting tokens: {e}")
        
        return bought_mints
    
    async def scan_new_transactions(self, wallet: str, limit: int = 10) -> List[str]:
        """
        Сканировать новые транзакции кошелька и вернуть купленные токены
        
        Returns:
            List[str]: Список mint адресов токенов которые были куплены
        """
        new_tokens = []
        
        # Получаем последние транзакции
        signatures = await self.get_recent_signatures(wallet, limit)
        
        for sig_info in signatures:
            sig = sig_info.get("signature")
            if not sig:
                continue
            
            # Пропускаем уже обработанные транзакции
            if sig in self.seen_signatures[wallet]:
                continue
            
            # Помечаем как обработанную
            self.seen_signatures[wallet].add(sig)
            
            # Получаем детали транзакции
            tx_data = await self.get_transaction(sig)
            if not tx_data:
                continue
            
            # Извлекаем купленные токены
            bought_tokens = self.extract_bought_tokens(tx_data)
            
            for mint in bought_tokens:
                # Добавляем только если ещё не видели этот токен
                if mint not in self.processed_tokens:
                    new_tokens.append(mint)
                    self.processed_tokens.add(mint)
                    logger.info(f"🔔 NEW TOKEN from {wallet[:8]}: {mint}")
        
        return new_tokens
    
    async def scan_all_wallets(self, limit: int = 10) -> List[tuple]:
        """
        Сканировать все кошельки и вернуть новые токены
        
        Returns:
            List[tuple]: [(wallet, mint), ...]
        """
        all_new_tokens = []
        
        for wallet in WATCH_WALLETS:
            try:
                new_mints = await self.scan_new_transactions(wallet, limit)
                for mint in new_mints:
                    all_new_tokens.append((wallet, mint))
            except Exception as e:
                logger.error(f"Error scanning wallet {wallet[:8]}: {e}")
        
        return all_new_tokens

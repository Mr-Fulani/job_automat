"""Интерфейс для сервисов отклика на вакансии."""
from abc import ABC, abstractmethod
from typing import Dict

from playwright.async_api import Page


class ApplyServiceInterface(ABC):
    """Абстрактный интерфейс для сервисов отклика."""
    
    @abstractmethod
    async def apply(self, url: str, message: str = "") -> Dict:
        """
        Отклик на вакансию.
        
        Args:
            url: URL вакансии
            message: Сопроводительное письмо
            
        Returns:
            Dict с полями status и message
        """
        pass

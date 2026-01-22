"""Модель профиля кандидата для Web-Use автоматизации."""
from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, validator


class CandidateProfile(BaseModel):
    """Данные профиля для автоматических откликов на вакансии."""
    
    full_name: str = Field(..., description="Полное имя кандидата")
    email: str = Field(..., description="Email адрес")
    phone: str = Field(..., description="Номер телефона")
    city: str = Field(..., description="Текущий город")
    birth_date: date = Field(..., description="Дата рождения")
    experience_years: int = Field(..., ge=0, description="Лет опыта")
    position: str = Field(..., description="Целевая должность")
    skills: List[str] = Field(..., description="Список навыков")
    work_format: List[str] = Field(..., description="Предпочитаемый формат работы")
    salary_expectations: str = Field(..., description="Ожидания по зарплате")
    cover_letter_template: str = Field(
        ..., 
        description="Шаблон сопроводительного письма с плейсхолдерами {experience_years}, {position}, {skills}"
    )
    answers: Dict[str, str] = Field(
        default_factory=dict,
        description="Заранее подготовленные ответы на частые вопросы"
    )
    
    @validator('email')
    def validate_email(cls, v):
        if '@' not in v:
            raise ValueError('Некорректный формат email')
        return v
    
    @validator('phone')
    def validate_phone(cls, v):
        # Базовая валидация телефона - должен содержать цифры и опционально +, -, пробелы, скобки
        import re
        if not re.match(r'^[\d\s\-\+\(\)]+$', v):
            raise ValueError('Некорректный формат телефона')
        return v
    
    @validator('skills')
    def validate_skills(cls, v):
        if not v:
            raise ValueError('Список навыков не может быть пустым')
        return [skill.strip() for skill in v if skill.strip()]
    
    @validator('work_format')
    def validate_work_format(cls, v):
        allowed_formats = ['удалёнка', 'гибрид', 'офис', 'remote', 'hybrid', 'office']
        for fmt in v:
            if fmt.lower() not in allowed_formats:
                raise ValueError(f'Некорректный формат работы: {fmt}')
        return v

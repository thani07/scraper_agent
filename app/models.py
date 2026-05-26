"""Pydantic models for job data extraction and site configuration."""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ATSStrategy(str, Enum):
    WORKDAY = "workday"
    GREENHOUSE = "greenhouse"
    ICIMS = "icims"
    LEVER = "lever"
    ULTIPRO = "ultipro"
    VIDESKTOP = "videsktop"
    FLORECRUIT = "florecruit"
    DIRECT = "direct"


class SiteConfig(BaseModel):
    """Configuration for a single law firm career site."""
    name: str = Field(description="Law firm name")
    careers_url: str = Field(description="Base careers page URL")
    strategy: ATSStrategy = Field(description="ATS platform type")
    navigation_hints: Optional[str] = Field(
        default=None,
        description="Extra hints for the agent e.g. 'Click Apply filters after entering role'"
    )


class JobExtraction(BaseModel):
    """Schema the LLM agent extracts from each job listing page."""
    role_title: str = Field(description="Exact job title as shown on the page")
    description: Optional[str] = Field(default=None, description="2-4 sentence summary of the role's responsibilities and purpose")
    salary_min: Optional[str] = Field(default=None, description="Minimum salary or base pay (e.g. '$180,000')")
    salary_max: Optional[str] = Field(default=None, description="Maximum salary or top of range (e.g. '$220,000')")
    salary_raw: Optional[str] = Field(default=None, description="Raw salary text as displayed on page if structured min/max not available")
    is_hourly: Optional[bool] = Field(default=None, description="True if salary is listed as an hourly rate; omit or null if annual/not listed")
    experience_years: Optional[str] = Field(default=None, description="Years of experience required (e.g. '5-7 years')")
    experience_raw: Optional[str] = Field(default=None, description="Raw experience text as displayed on page")
    location: Optional[str] = Field(default=None, description="Job location (city, state or remote)")
    job_url: str = Field(description="The URL of the page where this data was extracted from")
    practice_area: Optional[str] = Field(default=None, description="Practice area or department (e.g. 'Corporate', 'Litigation')")


class ScrapeResult(BaseModel):
    """Final result stored in Cosmos DB."""
    firm_name: str
    strategy_used: str
    role_searched: str
    extraction: Optional[JobExtraction] = None
    status: str = Field(default="success", description="success | no_results | error")
    error_message: Optional[str] = None
    scrape_duration_sec: Optional[float] = None

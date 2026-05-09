import re
from typing import Optional


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_email(text: str) -> Optional[str]:
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else None


def _extract_phone(text: str) -> Optional[str]:
    # Matches: +91-9876543210, 9876543210, +1 (555) 123-4567, etc.
    match = re.search(
        r"(\+?\d{1,3}[\s\-]?)?(\(?\d{3}\)?[\s\-]?)?\d{3}[\s\-]?\d{4,7}", text
    )
    return match.group(0).strip() if match else None


def _extract_name(lines: list[str], email: Optional[str], phone: Optional[str]) -> Optional[str]:
    """
    Heuristic: the name is usually the first non-empty line that is:
    - not an email
    - not a phone number
    - not a URL
    - not all caps section header (SKILLS, EDUCATION, etc.)
    - 2–5 words
    """
    skip_keywords = {
        "resume", "cv", "curriculum vitae", "profile", "contact",
        "summary", "objective", "experience", "education", "skills",
        "certifications", "projects", "references",
    }
    for line in lines[:10]:
        line = line.strip()
        if not line:
            continue
        if email and email.lower() in line.lower():
            continue
        if phone and phone in line:
            continue
        if re.search(r"https?://|www\.", line, re.IGNORECASE):
            continue
        if line.lower() in skip_keywords:
            continue
        words = line.split()
        if 2 <= len(words) <= 5 and all(re.match(r"[A-Za-z\.\-']+$", w) for w in words):
            return line
    return None


def _extract_section(text: str, *headers: str) -> Optional[str]:
    """
    Extract the block of text that follows any of the given section headers
    until the next known section header or end of text.
    """
    all_headers = [
        "SKILLS", "EDUCATION", "EXPERIENCE", "WORK EXPERIENCE",
        "CERTIFICATIONS", "PROJECTS", "SUMMARY", "OBJECTIVE",
        "LANGUAGES", "REFERENCES", "CONTACT", "PROFILE",
    ]
    pattern = "|".join(re.escape(h) for h in headers)
    stop_pattern = "|".join(
        re.escape(h) for h in all_headers if h not in headers
    )

    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None

    start = match.end()
    stop_match = re.search(stop_pattern, text[start:], re.IGNORECASE)
    end = start + stop_match.start() if stop_match else len(text)

    section_text = text[start:end].strip()
    # Clean leading colon/dash
    section_text = re.sub(r"^[\s:|\-]+", "", section_text).strip()
    return section_text if section_text else None


def _extract_skills(text: str) -> list[str]:
    skills_block = _extract_section(text, "SKILLS", "TECHNICAL SKILLS", "CORE COMPETENCIES")
    if not skills_block:
        return []
    # Split by comma, bullet, newline, pipe
    raw = re.split(r"[,\n•|/]+", skills_block)
    skills = [s.strip() for s in raw if s.strip() and len(s.strip()) > 1]
    return skills[:20]  # cap at 20


def _approx_experience_years(experience_text: Optional[str]) -> Optional[int]:
    """Attempt to count years from experience block (e.g. '2019 - 2022', '3 years')."""
    if not experience_text:
        return None
    # "X years" pattern
    match = re.search(r"(\d+)\s*\+?\s*years?", experience_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    # Count year ranges like 2018 – 2022
    year_pairs = re.findall(r"(20\d{2}|19\d{2})\s*[\-–—to]+\s*(20\d{2}|19\d{2}|present)", experience_text, re.IGNORECASE)
    if year_pairs:
        total = 0
        import datetime
        current_year = datetime.datetime.now().year
        for start_yr, end_yr in year_pairs:
            start = int(start_yr)
            end = current_year if end_yr.lower() == "present" else int(end_yr)
            total += max(0, end - start)
        return total if total > 0 else None
    return None


# ─── Public API ───────────────────────────────────────────────────────────────

def parse_resume_text(text: str) -> dict:
    """
    Parse raw resume text into a structured dict.
    Returns keys: name, email, phone, skills, education, experience, experience_years
    All fields are optional — missing data returns None / empty list.
    """
    lines = [l.strip() for l in text.splitlines()]

    email = _extract_email(text)
    phone = _extract_phone(text)
    name = _extract_name(lines, email, phone)
    skills = _extract_skills(text)
    education = _extract_section(text, "EDUCATION", "ACADEMIC BACKGROUND", "QUALIFICATIONS")
    experience = _extract_section(text, "EXPERIENCE", "WORK EXPERIENCE", "EMPLOYMENT", "PROFESSIONAL EXPERIENCE")
    experience_years = _approx_experience_years(experience)

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "skills": skills,
        "education": education,
        "experience": experience,
        "experience_years": experience_years,
    }

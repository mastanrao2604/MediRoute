from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT
import os


def _safe(value, fallback="") -> str:
    """Return value as string, or fallback if None/empty."""
    return (value or fallback).strip() or fallback


def generate_resume_pdf(data, file_path):
    doc = SimpleDocTemplate(
        file_path,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    styles = getSampleStyleSheet()

    section_style = ParagraphStyle(
        "Section",
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=colors.HexColor("#1e3a5f"),
        spaceAfter=4,
        leading=10,
    )
    body_style = ParagraphStyle(
        "Body",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#333333"),
    )
    bullet_style = ParagraphStyle(
        "Bullet",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        leftIndent=8,
        textColor=colors.HexColor("#444444"),
    )
    name_style = ParagraphStyle(
        "Name",
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=colors.white,
        leading=20,
    )
    contact_style = ParagraphStyle(
        "Contact",
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#a8c4e0"),
        leading=13,
    )

    def section_header(title):
        return [
            Paragraph(title.upper(), section_style),
            Spacer(1, 1),
        ]

    # ── LEFT COLUMN ──────────────────────────────────────────────────────────
    left_content = []

    photo_url = _safe(getattr(data, "photo_url", ""))
    if photo_url and os.path.exists(photo_url):
        try:
            img = Image(photo_url, width=70, height=70)
            left_content.append(img)
            left_content.append(Spacer(1, 10))
        except Exception:
            pass  # skip photo if it can't be loaded

    contact_parts = [
        _safe(getattr(data, "phone", "")),
        _safe(getattr(data, "email", "")),
        _safe(getattr(data, "location", "")),
    ]
    contact_parts = [p for p in contact_parts if p]
    if contact_parts:
        left_content += section_header("Contact")
        for part in contact_parts:
            left_content.append(Paragraph(part, body_style))
        left_content.append(Spacer(1, 10))

    skills_raw = _safe(getattr(data, "skills", ""))
    if skills_raw:
        left_content += section_header("Skills")
        for skill in skills_raw.split(","):
            skill = skill.strip()
            if skill:
                left_content.append(Paragraph(f"• {skill}", bullet_style))
        left_content.append(Spacer(1, 10))

    languages_raw = _safe(getattr(data, "languages", ""))
    if languages_raw:
        left_content += section_header("Languages")
        for lang in languages_raw.split(","):
            lang = lang.strip()
            if lang:
                left_content.append(Paragraph(f"• {lang}", bullet_style))

    # ── RIGHT COLUMN ─────────────────────────────────────────────────────────
    right_content = []

    full_name = _safe(getattr(data, "full_name", ""), "—")
    right_content.append(Paragraph(full_name, name_style))
    right_content.append(Spacer(1, 12))

    summary = _safe(getattr(data, "profile_summary", ""))
    if summary:
        right_content += section_header("Profile Summary")
        right_content.append(Paragraph(summary, body_style))
        right_content.append(Spacer(1, 10))

    experience = _safe(getattr(data, "experience", ""))
    if experience:
        right_content += section_header("Work Experience")
        for line in experience.splitlines():
            line = line.strip()
            if not line:
                right_content.append(Spacer(1, 4))
            elif line.startswith("•") or line.startswith("-"):
                right_content.append(Paragraph(line, bullet_style))
            else:
                right_content.append(Paragraph(f"<b>{line}</b>", body_style))
        right_content.append(Spacer(1, 10))

    education = _safe(getattr(data, "education", ""))
    if education:
        right_content += section_header("Education")
        for line in education.splitlines():
            line = line.strip()
            if line:
                right_content.append(Paragraph(line, body_style))

    # ── TWO-COLUMN TABLE ─────────────────────────────────────────────────────
    table = Table(
        [[left_content, right_content]],
        colWidths=[140, 370],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
        ("BACKGROUND", (1, 0), (1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEAFTER", (0, 0), (0, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]))

    # Header banner
    header_name = _safe(getattr(data, "full_name", ""), "—")
    contact_line = " · ".join(
        p for p in [
            _safe(getattr(data, "phone", "")),
            _safe(getattr(data, "email", "")),
            _safe(getattr(data, "location", "")),
        ] if p
    )
    header_table = Table(
        [[Paragraph(header_name, name_style)],
         [Paragraph(contact_line or " ", contact_style)]],
        colWidths=[530],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1e3a5f")),
        ("LEFTPADDING", (0, 0), (-1, -1), 18),
        ("RIGHTPADDING", (0, 0), (-1, -1), 18),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        ("TOPPADDING", (0, 1), (0, 1), 0),
        ("BOTTOMPADDING", (0, 1), (0, 1), 14),
    ]))

    doc.build([header_table, table])


def generate_german_pdf(data, file_path):
    doc = SimpleDocTemplate(file_path)
    styles = getSampleStyleSheet()

    story = []

    story.append(Paragraph("<b>Lebenslauf</b>", styles["Title"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph(f"Name: {_safe(getattr(data, 'full_name', ''))}", styles["Normal"]))
    story.append(Paragraph(f"Ort: {_safe(getattr(data, 'location', ''))}", styles["Normal"]))
    story.append(Spacer(1, 12))

    summary = _safe(getattr(data, "profile_summary", ""))
    if summary:
        story.append(Paragraph("<b>Profil</b>", styles["Heading2"]))
        story.append(Paragraph(summary, styles["Normal"]))
        story.append(Spacer(1, 12))

    experience = _safe(getattr(data, "experience", ""))
    if experience:
        story.append(Paragraph("<b>Berufserfahrung</b>", styles["Heading2"]))
        story.append(Paragraph(experience, styles["Normal"]))
        story.append(Spacer(1, 12))

    education = _safe(getattr(data, "education", ""))
    if education:
        story.append(Paragraph("<b>Ausbildung</b>", styles["Heading2"]))
        story.append(Paragraph(education, styles["Normal"]))

    doc.build(story)
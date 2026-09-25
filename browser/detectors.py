from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup


@dataclass
class FieldSpec:
    name: str
    field_type: str  # text, email, tel, url, select, radio, checkbox, textarea, file, hidden
    label: str = ""
    required: bool = False
    value: str | None = None
    options: list[str] = field(default_factory=list)
    aria_label: str | None = None
    placeholder: str | None = None
    element_id: str | None = None


@dataclass
class FormSpec:
    action: str
    method: str
    fields: list[FieldSpec]
    form_index: int = 0


class FormDetector:
    def detect(self, html: str, base_url: str = "") -> list[FormSpec]:
        soup = BeautifulSoup(html, "html.parser")
        forms: list[FormSpec] = []
        for idx, form in enumerate(soup.find_all("form")):
            action = form.get("action") or ""
            if action and not action.startswith("http") and base_url:
                from urllib.parse import urljoin

                action = urljoin(base_url, action)
            fields: list[FieldSpec] = []
            for el in form.find_all(["input", "select", "textarea"]):
                tag = el.name
                ftype = (el.get("type") or ("text" if tag != "select" and tag != "textarea" else tag)).lower()
                if ftype in ("submit", "button", "image", "reset"):
                    continue
                name = el.get("name")
                if not name:
                    continue
                label = self._label_for(soup, el)
                fields.append(
                    FieldSpec(
                        name=name,
                        field_type="select" if tag == "select" else ("textarea" if tag == "textarea" else ftype),
                        label=label,
                        required=el.has_attr("required"),
                        value=el.get("value"),
                        options=[o.get_text(strip=True) for o in el.find_all("option")] if tag == "select" else [],
                        aria_label=el.get("aria-label"),
                        placeholder=el.get("placeholder"),
                        element_id=el.get("id"),
                    )
                )
            forms.append(FormSpec(action=action, method=(form.get("method") or "get").lower(), fields=fields, form_index=idx))
        return forms

    def _label_for(self, soup: BeautifulSoup, el) -> str:
        el_id = el.get("id")
        if el_id:
            lab = soup.find("label", attrs={"for": el_id})
            if lab:
                return lab.get_text(" ", strip=True)
        parent = el.find_parent("label")
        if parent:
            return parent.get_text(" ", strip=True)
        if el.get("aria-label"):
            return el.get("aria-label")
        # nearby text before element
        prev = el.find_previous(["label", "span", "p", "legend"])
        if prev:
            text = prev.get_text(" ", strip=True)
            if text and len(text) < 120:
                return text
        return el.get("name", "")


class PageDetector:
    def detect(self, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True).lower()
        captcha = bool(soup.select_one("[data-testid=captcha], .captcha-challenge, [class*=captcha]")) or "captcha" in text
        mfa = bool(soup.select_one("[data-testid=mfa], .mfa-prompt, [class*=mfa]")) or any(
            t in text for t in ["enter the code sent", "two-factor", "verification code", "mfa"]
        )
        confirmation_el = soup.select_one("[data-testid=confirmation], [data-testid=application_id]")
        app_id_el = soup.select_one("[data-testid=application_id]")
        app_id = app_id_el.get_text(strip=True) if app_id_el else None
        if not app_id:
            m = re.search(r"\b(APP-[A-Z0-9]{6,})\b", soup.get_text(" ", strip=True))
            if m:
                app_id = m.group(1)
        success = bool(confirmation_el) or any(
            t in text for t in ["application received", "application submitted", "thank you for applying", "application id"]
        )
        errors = [li.get_text(strip=True) for li in soup.select("[data-testid=errors] li, .error, ul.errors li")]
        if not errors and "validation error" in text:
            errors = ["validation errors present"]
        has_form = bool(soup.find("form"))
        return {
            "has_form": has_form,
            "captcha": captcha,
            "mfa": mfa,
            "success": success,
            "application_id": app_id,
            "errors": errors,
            "title": (soup.title.get_text(strip=True) if soup.title else None),
            "text": soup.get_text(" ", strip=True)[:4000],
        }
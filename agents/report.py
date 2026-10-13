# -*- coding: utf-8 -*-
"""
종목분석 공유 리포트 — 차트 PNG(kaleido) + 한글 PDF(fpdf2 + 나눔고딕).

PDF는 앱 화면과 같은 구성: 상단 다크 헤더 → 지표 박스(현재가·점수) →
의견 카드 → 매매 가격 박스 → 펀더멘탈 칩 → 섹션 카드 → 차트 페이지.

공유 흐름: 앱의 📤 버튼 → 텔레그램(차트 사진 + PDF 문서) 전송 / PDF 다운로드.
텔레그램으로 받은 메시지·파일을 전달(Forward)하면 그대로 공유가 된다.
"""
from __future__ import annotations

import io
import re as _re
from datetime import datetime
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

_DISCLAIMER = ("본 리포트는 개인 참고용 자동 분석이며 투자 권유가 아닙니다. "
               "투자 판단과 책임은 본인에게 있습니다.")

_DARK = (23, 32, 48)        # 헤더 배경
_BOX = (245, 246, 248)      # 카드 배경
_GRAY = (120, 128, 138)
_TEXT = (33, 37, 41)


def fig_png(fig, width: int = 1000, height: int = 760, scale: int = 2) -> bytes:
    """plotly Figure → PNG 바이트 (kaleido)."""
    return fig.to_image(format="png", width=width, height=height, scale=scale)


def _clean(text: str) -> str:
    """나눔고딕에 없는 이모지 제거."""
    return _re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", str(text))


def _rgb(color) -> tuple[int, int, int]:
    """'#d32f2f' → (211,47,47). tuple이면 그대로, None이면 본문색."""
    if color is None:
        return _TEXT
    if isinstance(color, (tuple, list)):
        return tuple(color)
    c = str(color).lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def build_pdf(title: str, subtitle: str,
              sections: list[tuple[str, list[str]]],
              chart_png: bytes | None = None,
              meta: dict | None = None) -> bytes:
    """앱 화면 스타일 리포트 PDF.

    meta (선택):
      metrics: [(라벨, 값, 보조텍스트|None, 색|None)]  — 상단 지표 박스
      opinion: (문구, 색)                               — 참고 의견 카드
      prices:  [(라벨, 값, 보조|None, 색|None)]         — 매매 가격 박스
      chips:   [(텍스트, 색|None)]                      — 펀더멘탈 칩
    sections: [(섹션 제목, [줄, ...])] — 섹션 카드
    """
    from fpdf import FPDF

    meta = meta or {}
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(12, 12, 12)
    pdf.add_font("NG", "", str(_FONT_DIR / "NanumGothic-Regular.ttf"))
    pdf.add_font("NG", "B", str(_FONT_DIR / "NanumGothic-Bold.ttf"))
    W = 210 - 24  # 본문 폭

    def _header(page_title: str, sub: str = ""):
        pdf.add_page()
        pdf.set_fill_color(*_DARK)
        pdf.rect(0, 0, 210, 26, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("NG", "B", 15)
        pdf.set_xy(12, 6)
        pdf.cell(W, 8, _clean(page_title))
        if sub:
            pdf.set_font("NG", "", 7.5)
            pdf.set_text_color(165, 175, 190)
            pdf.set_xy(12, 15)
            pdf.cell(W, 5, _clean(sub)[:110])
        pdf.set_text_color(*_TEXT)
        pdf.set_y(32)

    def _boxes(items, box_h: float, big_size: float):
        """[(라벨, 값, 보조, 색)] → 한 줄 박스 그리드."""
        n = len(items)
        if not n:
            return
        gap = 2.5
        bw = (W - gap * (n - 1)) / n
        x, y = 12.0, pdf.get_y()
        for lab, val, sub, color in items:
            pdf.set_fill_color(*_BOX)
            pdf.rect(x, y, bw, box_h, "F")
            pdf.set_xy(x + 2.2, y + 2.2)
            pdf.set_font("NG", "", 6.5)
            pdf.set_text_color(*_GRAY)
            pdf.cell(bw - 4, 3, _clean(lab))
            pdf.set_xy(x + 2.2, y + 6.2)
            pdf.set_font("NG", "B", big_size)
            pdf.set_text_color(*_rgb(color))
            pdf.cell(bw - 4, 5.5, _clean(val))
            if sub:
                pdf.set_xy(x + 2.2, y + box_h - 4.6)
                pdf.set_font("NG", "", 6.8)
                pdf.set_text_color(*_rgb(color) if color else _GRAY)
                pdf.cell(bw - 4, 3.5, _clean(sub))
            x += bw + gap
        pdf.set_text_color(*_TEXT)
        pdf.set_y(y + box_h + 4)

    _header(title, subtitle)

    # ── 상단 지표 박스 ───────────────────────────────────────────────────
    _boxes(meta.get("metrics", []), box_h=17, big_size=10.5)

    # ── 참고 의견 카드 ───────────────────────────────────────────────────
    if meta.get("opinion"):
        op_text, op_color = meta["opinion"]
        y = pdf.get_y()
        pdf.set_fill_color(*_BOX)
        pdf.rect(12, y, W, 13, "F")
        pdf.set_fill_color(*_rgb(op_color))
        pdf.rect(12, y, 1.8, 13, "F")
        pdf.set_xy(16.5, y + 2)
        pdf.set_font("NG", "", 6.5)
        pdf.set_text_color(*_GRAY)
        pdf.cell(W - 8, 3, "참고 의견")
        pdf.set_xy(16.5, y + 5.6)
        pdf.set_font("NG", "B", 12)
        pdf.set_text_color(*_rgb(op_color))
        pdf.cell(W - 8, 6, _clean(op_text))
        pdf.set_text_color(*_TEXT)
        pdf.set_y(y + 13 + 4)

    # ── 매매 가격 박스 ───────────────────────────────────────────────────
    _boxes(meta.get("prices", []), box_h=16, big_size=10)

    # ── 펀더멘탈 칩 ─────────────────────────────────────────────────────
    if meta.get("chips"):
        x, y = 12.0, pdf.get_y()
        pdf.set_font("NG", "", 7.8)
        for chip in meta["chips"]:
            text, color = chip if isinstance(chip, (tuple, list)) else (chip, None)
            text = _clean(text)
            w = pdf.get_string_width(text) + 5
            if x + w > 12 + W:
                x, y = 12.0, y + 8
            pdf.set_fill_color(238, 240, 243)
            pdf.set_text_color(*_rgb(color))
            pdf.set_xy(x, y)
            pdf.cell(w, 6.2, text, fill=True, align="C")
            x += w + 2
        pdf.set_text_color(*_TEXT)
        pdf.set_y(y + 6.2 + 4)

    # ── 섹션 카드 ───────────────────────────────────────────────────────
    for heading, lines in sections:
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        y = pdf.get_y()
        if y > 255:
            _header(title, subtitle)
            y = pdf.get_y()
        pdf.set_fill_color(*_DARK)
        pdf.rect(12, y + 0.8, 2.2, 4.6, "F")
        pdf.set_xy(16.5, y)
        pdf.set_font("NG", "B", 10.5)
        pdf.cell(W - 6, 6, _clean(heading), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("NG", "", 8.8)
        pdf.set_x(16.5)
        for line in lines:
            pdf.set_x(16.5)
            pdf.multi_cell(W - 6, 5.0, _clean(line).replace("**", ""),
                           new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2.5)

    # ── 차트 페이지 ─────────────────────────────────────────────────────
    if chart_png:
        _header(title, "차트 — 최근 90거래일 · 일봉 / 이동평균 / 볼린저밴드 / 거래량 / RSI")
        pdf.image(io.BytesIO(chart_png), x=12, w=W)

    pdf.ln(4)
    pdf.set_font("NG", "", 7)
    pdf.set_text_color(*_GRAY)
    pdf.multi_cell(W, 4.3,
                   f"{_DISCLAIMER}\n생성: TradingAgents · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                   new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


def send_report(caption: str, png: bytes | None, pdf: bytes | None,
                pdf_name: str) -> tuple[bool, bool]:
    """텔레그램으로 (차트 사진, PDF 문서) 전송. 각 성공 여부 반환."""
    from .notify import send_telegram_document, send_telegram_photo
    ok_photo = send_telegram_photo(png, caption) if png else False
    ok_doc = send_telegram_document(pdf, pdf_name) if pdf else False
    return ok_photo, ok_doc

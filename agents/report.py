# -*- coding: utf-8 -*-
"""
종목분석 공유 리포트 — 차트 PNG(kaleido) + 한글 PDF(fpdf2 + 나눔고딕).

공유 흐름: 앱의 📤 버튼 → 텔레그램(차트 사진 + PDF 문서) 전송 / PDF 다운로드.
텔레그램으로 받은 메시지·파일을 전달(Forward)하면 그대로 공유가 된다.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

_DISCLAIMER = ("본 리포트는 개인 참고용 자동 분석이며 투자 권유가 아닙니다. "
               "투자 판단과 책임은 본인에게 있습니다.")


def fig_png(fig, width: int = 1000, height: int = 760, scale: int = 2) -> bytes:
    """plotly Figure → PNG 바이트 (kaleido)."""
    return fig.to_image(format="png", width=width, height=height, scale=scale)


def build_pdf(title: str, subtitle: str,
              sections: list[tuple[str, list[str]]],
              chart_png: bytes | None = None) -> bytes:
    """분석 리포트 PDF 생성.

    sections: [(섹션 제목, [줄, ...]), ...] — 줄은 평문 (마크다운 미지원)
    """
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_font("NG", "", str(_FONT_DIR / "NanumGothic-Regular.ttf"))
    pdf.add_font("NG", "B", str(_FONT_DIR / "NanumGothic-Bold.ttf"))
    pdf.add_page()

    import re as _re

    def _cell(h: float, text: str):
        # 나눔고딕에 없는 이모지 제거 + multi_cell 커서를 매번 왼쪽 여백·다음 줄로 리셋
        text = _re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", "", str(text))
        pdf.multi_cell(0, h, text, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("NG", "B", 17)
    _cell(9, title)
    pdf.set_font("NG", "", 9)
    pdf.set_text_color(110, 110, 110)
    _cell(5, subtitle)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    for heading, lines in sections:
        pdf.set_font("NG", "B", 11.5)
        _cell(7, heading)
        pdf.set_font("NG", "", 9.5)
        for line in lines:
            if line:
                _cell(5.4, str(line).replace("**", ""))
        pdf.ln(2)

    if chart_png:
        pdf.add_page()
        pdf.set_font("NG", "B", 11.5)
        _cell(7, "차트 (최근 90거래일 — 일봉·이동평균·볼린저밴드·거래량·RSI)")
        pdf.image(io.BytesIO(chart_png), w=182)

    pdf.ln(3)
    pdf.set_font("NG", "", 7.5)
    pdf.set_text_color(130, 130, 130)
    _cell(4.5, f"{_DISCLAIMER}\n생성: TradingAgents · {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return bytes(pdf.output())


def send_report(caption: str, png: bytes | None, pdf: bytes | None,
                pdf_name: str) -> tuple[bool, bool]:
    """텔레그램으로 (차트 사진, PDF 문서) 전송. 각 성공 여부 반환."""
    from .notify import send_telegram_document, send_telegram_photo
    ok_photo = send_telegram_photo(png, caption) if png else False
    ok_doc = send_telegram_document(pdf, pdf_name) if pdf else False
    return ok_photo, ok_doc

"""
Net/Gross proposal — PowerPoint export (Step 07).

A deliberately simple, signature-ready deck — NOT a full recreation of the
Excel proposal. One continuous, self-paginating table: a header block (once,
top of slide 1), then for EACH tier/option a colored section bar (skipped
entirely when there's only one tier) + its product rows + its own subtotal
row, then a signature block at the very end. Paginates automatically,
measured against real per-row content height (not a row-count guess), only
when a proposal is long enough to need it. Table stops at the Net/Gross
Budget column, same "not a contract detail" boundary the Excel print-area
change uses — no avails/notes here either. Built with manual shapes on a
blank layout throughout (not the default theme's placeholders) so every
color/font is exactly the Entravision spec below.

Design reference: two of the team's own hand-built decks (a single-slide
one and a 5-slide one) were reviewed for this — the useful, reusable ideas
from both (a colored bar naming which option a section is, targeting/geo
called out prominently near the top, impressions shown per product, a
tight 2-row signature block) are folded in below; the DOOH screen-inventory
list and a third-party SEM tool's own auto-generated slide from the 5-slide
example were NOT — those are one-off content for that specific deal, not a
reusable layout pattern.

Brand spec (as given):
  Background: white
  Logo: Entravision wordmark (local asset — see _LOGO_PATH)
  Main font: Poppins Bold | Secondary: Open Sans Regular | Headings: Open Sans Thin
  Palette: Primary #910e95, Secondary #ff003c, Tertiary #c800b4,
           Quaternary #0098e2, Quinary #ff6c02

Font rendering caveat (same as docx_builder.py's own): python-pptx can only
set a font NAME into the file — it can't embed the actual font. Poppins/
Open Sans render correctly on a machine that has them installed (or when
opened in PowerPoint/Google Slides' web font fallback); otherwise the
viewer substitutes its own default. "Open Sans Thin" specifically has no
native bold/weight toggle in pptx the way bold/italic do — this sets that
literal family name, which only renders as a true thin weight if that
exact static family is installed on the viewer's machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.shapes import MSO_SHAPE
    _HAS_PPTX = True
except ImportError:
    _HAS_PPTX = False

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# The user's given URL (EVC_Claim_Purple.png) 404'd when fetched — this is
# the closest already-vetted, real Entravision brand asset in the repo (the
# purple gradient "e" mark used elsewhere in this app) used as a stand-in.
# Swap this file for the real wordmark lockup the moment it's available;
# nothing else about the deck needs to change.
_LOGO_PATH = _STATIC_DIR / "entravision-logo.png"

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------

if _HAS_PPTX:
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    INK = RGBColor(0x22, 0x1A, 0x24)
    INK_SOFT = RGBColor(0x5A, 0x52, 0x5C)
    PRIMARY = RGBColor(0x91, 0x0E, 0x95)
    SECONDARY = RGBColor(0xFF, 0x00, 0x3C)
    TERTIARY = RGBColor(0xC8, 0x00, 0xB4)
    QUATERNARY = RGBColor(0x00, 0x98, 0xE2)
    QUINARY = RGBColor(0xFF, 0x6C, 0x02)
    ROW_ALT_FILL = RGBColor(0xF7, 0xEF, 0xF7)  # a very light tint of PRIMARY, for zebra striping
    RULE = RGBColor(0xE0, 0xD8, 0xE0)

    FONT_MAIN = "Poppins"        # bold headline/emphasis text
    FONT_BODY = "Open Sans"      # regular body/table text
    FONT_HEADING = "Open Sans Thin"  # section headings

    SLIDE_W = Inches(13.333)
    SLIDE_H = Inches(7.5)
    MARGIN = Inches(0.5)
    CONTENT_W = SLIDE_W - 2 * MARGIN
    FOOTER_Y = SLIDE_H - Inches(0.4)

    # --- Table column widths (in inches, sum must equal CONTENT_W's 12.333") ---
    # A real export surfaced what happens when these DON'T sum correctly:
    # the leftover/shortfall silently lands on whichever column absorbs the
    # remainder (Total ended up ~0.53" wide once — barely enough for "Tota",
    # let alone a real dollar figure). Verify this list sums to 12.333 after
    # any future edit — nothing auto-corrects a mismatch here.
    _COL_WIDTHS_IN = [3.6, 3.6, 0.9, 2.1, 2.133]  # Product, Target, Months, Monthly Budget, Total
    assert abs(sum(_COL_WIDTHS_IN) - 12.333) < 0.01, "pptx table column widths must sum to CONTENT_W"

    # Y where content starts: slide 1 has the full header block below it;
    # slide 2+ only has a small logo, so content can start much higher.
    HEADER_CONTENT_TOP_IN = 2.75
    NO_HEADER_CONTENT_TOP_IN = 0.85
    FOOTER_Y_IN = FOOTER_Y / 914400  # EMU -> inches, so this stays a plain float everywhere else

    # Row heights are computed per-row from real content (see _row_height_in
    # and _TIER_BAR_HEIGHT_IN/_SUBTOTAL_HEIGHT_IN below) — NOT a flat guess.
    # An earlier flat-row-count version still overflowed a real export (a
    # totals row that landed on top of the signature block; a longer-text
    # version that overflowed the slide entirely) because six rows of TEXT
    # is not a fixed height — it depends on how much of it wraps. Pagination
    # measures the actual height each candidate chunk needs against the
    # actual space available and only adds a row while it still fits.
    _TIER_BAR_HEIGHT_IN = 0.36
    _SUBTOTAL_HEIGHT_IN = 0.34
    # Vertical gap _add_table leaves between the table's last row and
    # whatever is drawn after it (the signature block, when this is the
    # last slide) — named so the pagination fit-check below and _add_table
    # itself can't silently drift apart the way they briefly did once
    # already (a tight-fit deck's signature block overlapped the footer by
    # exactly this much before it got its own named constant).
    _TABLE_TO_SIGNATURE_GAP_IN = 0.15


def _set_background_white(slide) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = WHITE


def _add_textbox(slide, left, top, width, height, text, *, font=None,
                  size=12, bold=False, color=None, align=None,
                  anchor=None, wrap=True):
    font = font or FONT_BODY
    color = color if color is not None else INK
    align = align if align is not None else PP_ALIGN.LEFT
    anchor = anchor if anchor is not None else MSO_ANCHOR.TOP
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def _add_rule(slide, left, top, width, *, height=None, color=None):
    height = height if height is not None else Pt(3)
    color = color if color is not None else PRIMARY
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape


def _add_logo(slide, *, left=None, top=None, height=None):
    left = left if left is not None else MARGIN
    top = top if top is not None else Inches(0.35)
    height = height if height is not None else Inches(0.4)
    if _LOGO_PATH.exists():
        try:
            slide.shapes.add_picture(str(_LOGO_PATH), left, top, height=height)
            return
        except Exception:
            pass
    # Graceful text fallback if the asset is ever missing — never let a
    # missing logo file break the whole export.
    _add_textbox(slide, left, top, Inches(3), height, "ENTRAVISION",
                 font=FONT_MAIN, size=16, bold=True, color=PRIMARY)


def _footer(slide, page_label: str):
    _add_textbox(slide, MARGIN, FOOTER_Y, Inches(8), Inches(0.3),
                 "Entravision Communications Corporation", font=FONT_BODY, size=8, color=INK_SOFT)
    if page_label:
        _add_textbox(slide, SLIDE_W - MARGIN - Inches(2), FOOTER_Y, Inches(2), Inches(0.3),
                     page_label, font=FONT_BODY, size=8, color=INK_SOFT, align=PP_ALIGN.RIGHT)


def _add_palette_strip(slide):
    """A light brand touch along the very bottom edge — not a data element."""
    strip_colors = [PRIMARY, TERTIARY, SECONDARY, QUATERNARY, QUINARY]
    strip_w = SLIDE_W / len(strip_colors)
    for i, c in enumerate(strip_colors):
        _add_rule(slide, Emu(int(strip_w * i)), SLIDE_H - Inches(0.1), Emu(int(strip_w)) + Emu(1),
                  height=Inches(0.1), color=c)


# ---------------------------------------------------------------------------
# Header block — cover info, shown only on the first slide. Both reference
# decks reviewed for this lead with a prominent campaign-level targeting/geo
# line right under the client name — folded in here as its own line rather
# than only relying on each row's own (often-blank) per-line target text.
# ---------------------------------------------------------------------------

def _add_header(slide, *, proposal_title: str, client_name: str, requested_by: str,
                start_date: str, end_date: str, gross: bool, target_summary: str,
                geo: str) -> float:
    """Returns the Y position (inches, as a float) where content below the
    header should start."""
    _add_logo(slide)

    kicker = "GROSS MEDIA PROPOSAL" if gross else "NET MEDIA PROPOSAL"
    _add_textbox(slide, MARGIN, Inches(0.85), Inches(10), Inches(0.3), kicker,
                 font=FONT_HEADING, size=13, color=SECONDARY)
    _add_textbox(slide, MARGIN, Inches(1.15), CONTENT_W, Inches(0.45),
                 client_name or "Client", font=FONT_MAIN, size=24, bold=True, color=PRIMARY)

    flight = f"  ·  {start_date} – {end_date}" if (start_date and end_date) else ""
    subtitle = f"Prepared by {requested_by or 'Entravision'}{flight}"
    _add_textbox(slide, MARGIN, Inches(1.62), CONTENT_W, Inches(0.3),
                 subtitle, font=FONT_BODY, size=10.5, color=INK_SOFT)

    # Campaign-level targeting/geo — its own visible line, not buried.
    target_line_parts = []
    if target_summary and target_summary != "TBD":
        target_line_parts.append(f"Target: {target_summary}")
    if geo:
        target_line_parts.append(f"Geo: {geo}")
    if target_line_parts:
        _add_textbox(slide, MARGIN, Inches(1.92), CONTENT_W, Inches(0.4),
                     "  ·  ".join(target_line_parts), font=FONT_BODY, size=10, bold=True, color=INK)

    _add_textbox(slide, MARGIN, Inches(2.35), CONTENT_W, Inches(0.25),
                 proposal_title, font=FONT_BODY, size=9, color=INK_SOFT)

    _add_rule(slide, MARGIN, Inches(2.65), Inches(1.6), color=QUINARY)
    return HEADER_CONTENT_TOP_IN


# ---------------------------------------------------------------------------
# Row-height estimation — used both to size table rows and to decide
# pagination, so the two can never drift out of sync with each other.
# ---------------------------------------------------------------------------

def _estimate_wrapped_lines(text: str, *, chars_per_line: int = 38, max_lines: int = 40) -> int:
    """How many lines `text` will wrap to in a ~3.6"-wide column at 10-11pt
    Open Sans. chars_per_line is a deliberately conservative estimate
    (rounded down from a rough 0.085"/char average) — better to slightly
    over-book a row's height than under-book it and have PowerPoint's own
    auto-fit silently grow a row taller than this file calculated."""
    if not text:
        return 1
    # Per paragraph: a hard line break (Step 04's target box is a textarea) always starts a new visual line.
    lines = sum(max(1, -(-len(par) // chars_per_line)) for par in text.split("\n"))
    return max(1, min(lines, max_lines))


def _line_item_impressions(product, li) -> Optional[int]:
    """Best-effort monthly impressions for ONE line item — same math Excel
    itself uses (see excel_template._write_product_row / ai_enricher.py's
    _estimate_monthly_impressions, which does the same thing summed across
    a whole set rather than per-line). None when this product's buying
    model doesn't support an impressions estimate (e.g. CPP, or no rate
    on file) — shown as nothing rather than a fabricated number. Also None
    for an Added Value line: $0 real budget can't drive a real impressions
    estimate, and "Est. 0 impressions/mo" would read as a data error next
    to a line that's deliberately $0 — the "Added Value" budget-column tag
    already says everything worth saying about that line."""
    if li.is_added_value:
        return None
    rate = li.rate_override if li.rate_override is not None else product.base_rate
    if product.buying_model == "CPM" and rate:
        return round(li.monthly_budget / rate * 1000)
    effective_cpm = li.estimated_cpm_override if li.estimated_cpm_override is not None else product.estimated_cpm_for_imps
    if (product.buying_model == "Fixed" or product.estimated_impressions) and effective_cpm:
        return round(li.monthly_budget / effective_cpm * 1000)
    return None


def _product_cell_text(product, li, unit_suffix: str = "/mo") -> str:
    imps = _line_item_impressions(product, li)
    if imps is None:
        return product.name
    return f"{product.name}\nEst. {imps:,} impressions{unit_suffix}"


def _row_height_in(product_name: str, target_text: str, *, header: bool = False,
                   has_impressions: bool = False) -> float:
    if header:
        return 0.34
    name_lines = _estimate_wrapped_lines(product_name) + (1 if has_impressions else 0)
    lines = max(name_lines, _estimate_wrapped_lines(target_text))
    return 0.19 * lines + 0.1  # per-line height + fixed cell padding (top+bottom margins)


# ---------------------------------------------------------------------------
# The flat, packable item sequence — one entry per table ROW across the
# WHOLE deck (every tier's bar + rows + subtotal, back to back). Pagination
# operates on this single sequence so a tier's rows can share a slide with
# the previous tier's tail end (still a one-sheeter for 2 short options)
# instead of always forcing a fresh slide per tier.
# ---------------------------------------------------------------------------

def _build_items(tiers: list, *, gross: bool, agency_fee: Optional[float]) -> list[dict]:
    show_tier_bars = len(tiers) > 1
    items: list[dict] = []
    for tier in tiers:
        pairs = list(zip(tier["products"], tier["line_items"]))
        if not pairs:
            continue
        if show_tier_bars:
            items.append({"kind": "tier_bar", "label": tier["name"], "height": _TIER_BAR_HEIGHT_IN})
        tier_total = 0.0
        for product, li in pairs:
            monthly = li.monthly_budget
            line_total = monthly * li.months
            if gross and agency_fee:
                monthly = monthly / (1 - agency_fee)
                line_total = line_total / (1 - agency_fee)
            tier_total += line_total
            target = li.target_override or tier["target_fallback"]
            has_imps = _line_item_impressions(product, li) is not None
            items.append({
                "kind": "row", "product": product, "line_item": li,
                "monthly": monthly, "line_total": line_total, "target": target,
                "height": _row_height_in(product.name, target, has_impressions=has_imps),
            })
        items.append({
            "kind": "subtotal", "label": tier["name"] if show_tier_bars else "TOTAL",
            "total": tier_total, "height": _SUBTOTAL_HEIGHT_IN,
        })
    return items


def _greedy_chunk(items: list, *, start_idx: int, avail_height_in: float):
    """Fill as many items, starting at start_idx, as fit within
    avail_height_in — always packed as generously as the real content
    allows (no signature space held back here; the caller decides afterward
    whether the signature block also fits in whatever's left over, see
    build_signature_deck). Always takes at least one item even if it alone
    doesn't fit, so a single pathological row can't spin this into an
    infinite loop. A tier_bar is never left as the LAST item in a chunk (an
    orphaned heading with nothing under it) — unless it's genuinely the
    only thing that fits at all.
    Returns (chunk, next_idx, used_height_in)."""
    header_h = _row_height_in("", "", header=True)
    used = header_h
    chunk: list[dict] = []
    idx = start_idx
    while idx < len(items):
        item = items[idx]
        h = item["height"]
        if chunk and used + h > avail_height_in:
            break
        # Don't strand a tier_bar as the slide's last item — hold it (and
        # everything from here) for the next slide instead, UNLESS nothing
        # else has been placed yet on this one.
        if item["kind"] == "tier_bar" and chunk:
            remaining_after = items[idx + 1:idx + 2]
            if remaining_after:
                next_h = remaining_after[0]["height"]
                if used + h + next_h > avail_height_in:
                    break
        chunk.append(item)
        used += h
        idx += 1
    return chunk, idx, used


# Step 04's Week/Month/Quarter toggle, as the table's column headers and per-row budget suffix.
_UNIT_COPY = {
    "week": ("Weeks", "Weekly Budget", "/wk"),
    "month": ("Months", "Monthly Budget", "/mo"),
    "quarter": ("Quarters", "Quarterly Budget", "/qtr"),
}


def _add_table(slide, *, top_in: float, chunk: list, gross: bool, time_unit: str = "month") -> float:
    """Draws one continuous table for this slide's chunk of items (always
    starts with a column-header row). Returns the Y position (inches)
    after the table — the exact sum of the row heights this function
    itself just set, not a separate estimate that could drift."""
    unit_plural, unit_budget, unit_suffix = _UNIT_COPY.get(time_unit, _UNIT_COPY["month"])
    columns = ["Product", "Target", unit_plural, unit_budget, "Total"]
    header_h = _row_height_in("", "", header=True)
    row_heights_in = [header_h] + [item["height"] for item in chunk]
    n_rows = len(row_heights_in)

    gfx = slide.shapes.add_table(n_rows, len(columns), MARGIN, Inches(top_in), CONTENT_W, Inches(sum(row_heights_in)))
    table = gfx.table
    for i, w in enumerate(_COL_WIDTHS_IN):
        table.columns[i].width = Inches(w)
    for i, h in enumerate(row_heights_in):
        table.rows[i].height = Inches(h)

    def _cell(r, c, text, *, bold=False, color=None, align=None, size=11,
              font=None, fill=None):
        color = color if color is not None else INK
        align = align if align is not None else PP_ALIGN.LEFT
        font = font or FONT_BODY
        cell = table.cell(r, c)
        cell.margin_left = cell.margin_right = Pt(6)
        cell.margin_top = cell.margin_bottom = Pt(3)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill if fill is not None else WHITE
        tf = cell.text_frame
        tf.word_wrap = True
        # Multi-line cell text (product name + impressions sub-line) is two
        # differently-styled runs in two paragraphs, not one run — that's
        # only ever used for the impressions sub-line below, which stays
        # muted/small on purpose so it doesn't compete with the product name.
        lines = text.split("\n")
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = lines[0] or "\u00a0"
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        for extra in lines[1:]:
            sub_p = tf.add_paragraph()
            sub_p.alignment = align
            sub_run = sub_p.add_run()
            sub_run.text = extra
            sub_run.font.name = FONT_BODY
            sub_run.font.size = Pt(8.5)
            sub_run.font.bold = False
            sub_run.font.color.rgb = INK_SOFT

    def _merged_banner(r, text, *, fill):
        table.cell(r, 0).merge(table.cell(r, len(columns) - 1))
        _cell(r, 0, text, bold=True, color=WHITE, size=11, font=FONT_MAIN, fill=fill)

    for c, label in enumerate(columns):
        align = PP_ALIGN.LEFT if c < 2 else (PP_ALIGN.CENTER if c == 2 else PP_ALIGN.RIGHT)
        _cell(0, c, label, bold=True, color=WHITE, align=align, size=11, font=FONT_MAIN, fill=PRIMARY)

    data_row_i = 0  # for zebra striping — only counts real product rows
    for i, item in enumerate(chunk):
        r = i + 1
        if item["kind"] == "tier_bar":
            _merged_banner(r, item["label"], fill=SECONDARY)
        elif item["kind"] == "subtotal":
            label = item["label"] if item["label"] == "TOTAL" else f"{item['label']} — Total"
            _cell(r, 0, label, bold=True, color=WHITE, fill=TERTIARY)
            for c in (1, 2, 3):
                _cell(r, c, "", fill=TERTIARY)
            _cell(r, 4, f"${item['total']:,.0f}", bold=True, color=WHITE, align=PP_ALIGN.RIGHT, fill=TERTIARY)
        else:  # "row"
            product, li = item["product"], item["line_item"]
            fill = ROW_ALT_FILL if data_row_i % 2 == 1 else WHITE
            data_row_i += 1
            _cell(r, 0, _product_cell_text(product, li, unit_suffix), fill=fill)
            _cell(r, 1, item["target"], color=INK_SOFT, size=10, fill=fill)
            _cell(r, 2, str(li.months), align=PP_ALIGN.CENTER, fill=fill)
            _cell(r, 3, "Added Value" if li.is_added_value else f"${item['monthly']:,.0f}{unit_suffix}", align=PP_ALIGN.RIGHT, fill=fill)
            _cell(r, 4, f"${item['line_total']:,.0f}", align=PP_ALIGN.RIGHT, bold=True, fill=fill)

    return top_in + sum(row_heights_in) + _TABLE_TO_SIGNATURE_GAP_IN


# Legal footer copy -- the same wording the Excel proposal's footer carries
# (excel_template._write_addons_grand_total_footer), so the two deliverables
# can't disagree about what the client is agreeing to.
_VALIDITY_TEXT = (
    "This proposal will be valid for a period of 1 month after being presented. Please notify "
    "your Account Executive if you require the presented media to remain booked after that time."
)
_TERMS_PRE = "Client accepts Entravision's Terms of Sales ("
_TERMS_URL = "https://entravision.com/termsofsales/"
_TERMS_POST = ")"

# Geometry for the closing block, in inches. Banners and legal copy are 9pt
# Open Sans across the 12.33" content width; ~170 characters per line is a
# deliberately conservative estimate (same "rather over-book than clip"
# stance _estimate_wrapped_lines takes for table cells).
_NOTE_CHARS_PER_LINE = 170
_NOTE_LINE_IN = 0.16
_BANNER_PAD_IN = 0.14
_BANNER_GAP_IN = 0.08
_LEGAL_PARA_GAP_IN = 0.04
_INVESTMENT_ROW_IN = 0.42
_SIGNATURE_LABEL_ROW_IN = 0.38


def _note_lines(text: str) -> int:
    return sum(max(1, -(-len(par) // _NOTE_CHARS_PER_LINE)) for par in (text or "").split("\n"))


def _banner_height_in(banner: dict) -> float:
    return _BANNER_PAD_IN + _NOTE_LINE_IN * _note_lines(banner["text"])


def _signature_authorization_text(client_name: str, gross: bool) -> str:
    return (
        f"All rates are {'GROSS' if gross else 'NET'} unless marked otherwise. This proposal is not a guarantee of delivery. By signing below, "
        f"{client_name or 'the client'} authorizes Entravision Communications Corporation to "
        "proceed with the selected media plan."
    )


def _signature_layout(banners: list[dict], client_name: str, gross: bool) -> dict:
    """Vertical offsets (inches, relative to the block's top) for the closing
    block: matched disclaimer banners, legal copy, investment line, then the
    signature rules -- plus `total`, the height the whole thing needs, which
    pagination reads so a long banner list pushes the block onto its own
    slide instead of overflowing the footer. One function feeds both the
    drawing and the fit check, so they can't drift apart."""
    y = 0.0
    banner_tops = []
    for b in banners:
        banner_tops.append(y)
        y += _banner_height_in(b) + _BANNER_GAP_IN
    legal_top = y
    legal_lines = (
        _note_lines(_VALIDITY_TEXT)
        + _note_lines(_TERMS_PRE + _TERMS_URL + _TERMS_POST)
        + _note_lines(_signature_authorization_text(client_name, gross))
    )
    legal_h = legal_lines * _NOTE_LINE_IN + 2 * _LEGAL_PARA_GAP_IN
    investment_top = legal_top + legal_h + 0.08
    sig_top = investment_top + _INVESTMENT_ROW_IN
    return {
        "banner_tops": banner_tops, "legal_top": legal_top, "legal_h": legal_h,
        "investment_top": investment_top, "sig_top": sig_top,
        "total": sig_top + _SIGNATURE_LABEL_ROW_IN,
    }


def _add_banner(slide, top_in: float, banner: dict):
    """A colored, full-width callout (same pale-fill/dark-text pairing as the
    Excel banners). A real autoshape rather than a text box so the fill
    spans the whole width and the text stays inside it."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, MARGIN, Inches(top_in), CONTENT_W, Inches(_banner_height_in(banner)))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(banner["fill"].upper())
    shape.line.fill.background()
    shape.shadow.inherit = False
    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = Inches(0.12)
    tf.margin_top = tf.margin_bottom = Inches(0.04)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = banner["text"]
    run.font.name = FONT_BODY
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(banner["font_color"].upper())


def _add_legal_block(slide, top_in: float, height_in: float, client_name: str, gross: bool):
    """Validity + Terms of Sales (with a real clickable link) + authorization
    line, as three paragraphs in one text box."""
    box = slide.shapes.add_textbox(MARGIN, Inches(top_in), CONTENT_W, Inches(height_in))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0

    def _run(paragraph, text, *, link=None):
        run = paragraph.add_run()
        run.text = text
        run.font.name = FONT_BODY
        run.font.size = Pt(9)
        run.font.color.rgb = INK
        if link:
            run.hyperlink.address = link
        return run

    p1 = tf.paragraphs[0]
    p1.alignment = PP_ALIGN.LEFT
    p1.space_after = Inches(_LEGAL_PARA_GAP_IN)
    _run(p1, _VALIDITY_TEXT)

    p2 = tf.add_paragraph()
    p2.alignment = PP_ALIGN.LEFT
    p2.space_after = Inches(_LEGAL_PARA_GAP_IN)
    _run(p2, _TERMS_PRE)
    _run(p2, _TERMS_URL, link=_TERMS_URL)
    _run(p2, _TERMS_POST)

    p3 = tf.add_paragraph()
    p3.alignment = PP_ALIGN.LEFT
    _run(p3, _signature_authorization_text(client_name, gross))


def _add_signature_block(slide, *, client_name: str, top_in: float, tier_totals: list[tuple[str, float]],
                         banners: Optional[list[dict]] = None, gross: bool = False):
    """Closing block, top to bottom: matched disclaimer banners (directly
    below the table's totals), the legal copy, a compact investment line,
    then the client/signature/date rules. For more than one option, lists
    each option's own total instead of a single (meaningless, since they're
    alternatives not a sum) combined number."""
    banners = banners or []
    layout = _signature_layout(banners, client_name, gross)
    # tier_totals are whole-flight totals (budget x periods), so they carry no per-month suffix.
    if len(tier_totals) == 1:
        investment_line = f"Total Investment: ${tier_totals[0][1]:,.0f}"
    else:
        investment_line = "  \u00b7  ".join(f"{name}: ${total:,.0f}" for name, total in tier_totals)

    for banner, rel_top in zip(banners, layout["banner_tops"]):
        _add_banner(slide, top_in + rel_top, banner)
    _add_legal_block(slide, top_in + layout["legal_top"], layout["legal_h"], client_name, gross)
    _add_textbox(slide, MARGIN, Inches(top_in + layout["investment_top"]), CONTENT_W, Inches(0.3),
                 investment_line, font=FONT_MAIN, size=11, bold=True, color=PRIMARY)

    sig_top = top_in + layout["sig_top"]
    col_w = Inches(3.9)
    gap = Inches(0.2)
    labels = ["Client", "Signature", "Date"]
    for i, label in enumerate(labels):
        left = MARGIN + i * (col_w + gap)
        _add_rule(slide, left, Inches(sig_top), col_w, height=Pt(1), color=INK_SOFT)
        _add_textbox(slide, left, Inches(sig_top + 0.08), col_w, Inches(0.3),
                     label, font=FONT_BODY, size=9, color=INK_SOFT)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_signature_deck(
    request, tiers: list[dict], output_path: Path, *,
    gross: bool, proposal_title: str = "",
    banners: Optional[list[dict]] = None,
    time_unit: str = "month",
) -> bool:
    """
    Build the simple, signature-ready Net or Gross deck — one slide when the
    content is short enough (the common case), paginating automatically
    otherwise. Returns True on success, False if python-pptx isn't
    installed (graceful skip, same pattern as docx_builder.py — never
    raises to the caller for a missing optional dependency).

    tiers: [{"name": str, "products": list[Product], "line_items":
    list[LineItem]}, ...] — one entry per budget option. A single-tier
    proposal (the common case) renders exactly as before (no tier bar, one
    plain "TOTAL" row); more than one gets each option labeled with its own
    colored bar and its own subtotal — never a combined grand total across
    options, since they're alternatives, not additive.

    banners: matched admin disclaimers (app/disclaimers.py), each {"text",
    "fill", "font_color"} -- drawn as colored callouts at the top of the
    closing block, directly below the table's totals and above the legal
    copy; they flow onto a following slide rather than overflow. Already
    de-duplicated across options by the caller (the closing block is shared
    by every option).

    time_unit: Step 04's "week"/"month"/"quarter" toggle, for the table's
    column headers and per-row budget suffix.
    """
    if not _HAS_PPTX:
        return False
    tiers = [t for t in tiers if t.get("products") and t.get("line_items")]
    if not tiers:
        return False

    from app.services.notion_parser import compose_target_fallback
    target_fallback = compose_target_fallback(request)
    for t in tiers:
        t["target_fallback"] = target_fallback

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    client_name = getattr(request, "client_name", "") or ""
    requested_by = getattr(request, "requested_by", "") or ""
    start_date = getattr(request, "start_date", "") or ""
    end_date = getattr(request, "end_date", "") or ""
    geo = getattr(request, "geo", "") or ""
    agency_fee = getattr(request, "agency_fee", None)
    fee_for_gross = agency_fee if gross else None

    items = _build_items(tiers, gross=gross, agency_fee=fee_for_gross)
    banners = banners or []
    # The closing block (legal copy + investment line + signature rules) has a fixed height; banners are
    # placed one by one in whatever room is left, flowing onto a new slide when the next one doesn't fit
    # (their number and length are admin-controlled, so they can't be assumed to fit anywhere).
    signature_height_in = _signature_layout([], client_name, gross)["total"]

    # Phase 1 — decide what goes on each slide, measured against the REAL
    # height each candidate chunk needs, not a row-count guess. Every slide
    # is packed as generously as its real content allows (never held back
    # to make room for a signature block that might not even land there) —
    # that's what keeps a subtotal glued to its own rows instead of
    # stranded alone on a near-empty page. Only AFTER packing do we check
    # whether the signature block also fits in whatever's left over on the
    # final data slide; if it doesn't (a real, if narrow, near-miss — not
    # the common case), it gets one dedicated slide of its own rather than
    # being crammed in or silently dropped (an earlier version of this
    # shipped with the totals row vanishing entirely in that situation).
    slide_plans: list[dict] = []
    idx = 0
    while idx < len(items):
        is_first = len(slide_plans) == 0
        content_top = HEADER_CONTENT_TOP_IN if is_first else NO_HEADER_CONTENT_TOP_IN
        avail = FOOTER_Y_IN - content_top

        chunk, next_idx, used = _greedy_chunk(items, start_idx=idx, avail_height_in=avail)
        slide_plans.append({
            "chunk": chunk, "is_first": is_first, "is_last": False, "content_top": content_top, "banners": [],
            "room": avail - used - _TABLE_TO_SIGNATURE_GAP_IN,    # what's left under the table on this slide
        })
        idx = next_idx

    # Closing block: banners first (directly under the totals), then the legal copy / investment line /
    # signature rules as one unit. Anything that doesn't fit the room left moves to a fresh slide with no
    # table (an empty chunk signals "no table on this one" below).
    def _fresh_slide() -> dict:
        plan = {"chunk": [], "is_first": False, "is_last": False, "content_top": NO_HEADER_CONTENT_TOP_IN,
                "banners": [], "room": FOOTER_Y_IN - NO_HEADER_CONTENT_TOP_IN}
        slide_plans.append(plan)
        return plan

    current = slide_plans[-1]
    for banner in banners:
        need = _banner_height_in(banner) + _BANNER_GAP_IN
        if need > current["room"] and (current["chunk"] or current["banners"]):
            current = _fresh_slide()
        current["banners"].append(banner)
        current["room"] -= need
    if signature_height_in > current["room"] and (current["chunk"] or current["banners"]):
        current = _fresh_slide()
    current["is_last"] = True

    total_slides = len(slide_plans)
    # Recomputed directly per tier (not read back off the `items` subtotal
    # entries) so two tiers sharing a display name can't be conflated.
    tier_totals = []
    for t in tiers:
        pairs = list(zip(t["products"], t["line_items"]))
        total = 0.0
        for product, li in pairs:
            line_total = li.monthly_budget * li.months
            if gross and fee_for_gross:
                line_total = line_total / (1 - fee_for_gross)
            total += line_total
        tier_totals.append((t["name"], total))

    # Phase 2 — actually draw each slide from the plan above.
    for i, plan in enumerate(slide_plans):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _set_background_white(slide)

        if plan["is_first"]:
            _add_header(
                slide, proposal_title=proposal_title, client_name=client_name,
                requested_by=requested_by, start_date=start_date, end_date=end_date, gross=gross,
                target_summary=target_fallback, geo=geo,
            )
        else:
            _add_logo(slide, height=Inches(0.3))

        # An empty chunk means this slide exists solely to hold the
        # signature block (the data+subtotal already filled the previous
        # slide right up to the footer) — skip drawing a table with no
        # rows in it (just an orphaned column-header bar) and start the
        # signature block near the top instead.
        if plan["chunk"]:
            y_after = _add_table(slide, top_in=plan["content_top"], chunk=plan["chunk"], gross=gross, time_unit=time_unit)
        else:
            y_after = plan["content_top"]

        for banner in plan["banners"]:
            _add_banner(slide, y_after, banner)
            y_after += _banner_height_in(banner) + _BANNER_GAP_IN

        if plan["is_last"]:
            _add_signature_block(slide, client_name=client_name, top_in=y_after, tier_totals=tier_totals,
                                 banners=None, gross=gross)

        page_label = f"Media Plan {i + 1}/{total_slides}" if total_slides > 1 else "Media Plan"
        _footer(slide, page_label)
        if total_slides == 1:
            _add_palette_strip(slide)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return True

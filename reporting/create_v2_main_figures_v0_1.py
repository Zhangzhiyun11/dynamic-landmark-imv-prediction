#!/usr/bin/env python3
"""Create Study B/V2 publication figures from locked aggregate CSV files.

The script deliberately reads aggregate outputs only. It writes editable SVG and
high-resolution PNG copies into 09_manuscript/figures without touching the
immutable analytic lock.
"""

from __future__ import annotations

import csv
import html
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "07_results"
OUT = ROOT / "09_manuscript" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

INK = "#17212B"
BLUE = "#1665A6"
TEAL = "#008A83"
ORANGE = "#D87322"
RED = "#B23A48"
GRAY = "#6B7280"
LIGHT = "#E8EEF3"
WHITE = "#FFFFFF"


def font_path(bold: bool = False) -> str:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError("No suitable TrueType font was found")


def F(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(bold), size=size)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_svg(path: Path, width: int, height: int, body: str) -> None:
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'<rect width="100%" height="100%" fill="white"/>\n{body}\n</svg>\n',
        encoding="utf-8",
    )


def svg_text(x: float, y: float, text: str, size: int, *, weight: int = 400,
             anchor: str = "start", fill: str = INK) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{fill}">'
        f'{html.escape(text)}</text>'
    )


def arrow_svg(x1: int, y1: int, x2: int, y2: int, color: str = GRAY) -> str:
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="4"/>'
        f'<polygon points="{x2},{y2} {x2-16},{y2-9} {x2-16},{y2+9}" fill="{color}"/>'
    )


def draw_arrow(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], color: str = GRAY) -> None:
    x1, y1, x2, y2 = xy
    draw.line((x1, y1, x2, y2), fill=color, width=5)
    draw.polygon([(x2, y2), (x2 - 18, y2 - 10), (x2 - 18, y2 + 10)], fill=color)


def figure1() -> None:
    width, height = 2400, 1420
    im = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(im)
    title = "Figure 1. Study design and locked analysis workflow"
    d.text((90, 55), title, font=F(54, True), fill=INK)

    boxes = [
        (90, 190, 590, 490, "MIMIC-IV v3.1", ["Single academic centre", "31,833 ICU stays", "232,316 landmarks", "5 patient-grouped outer folds"], BLUE),
        (90, 560, 590, 860, "eICU-CRD v2.0", ["208 US hospitals", "78,635 ICU stays", "568,182 landmarks", "10 hospital-grouped IECV folds"], TEAL),
        (740, 360, 1300, 700, "Repeated landmark cohort", ["Landmarks: 24–72 h every 4 h", "Lookback: [t−12 h, t]", "Target: observed new IMV in (t, t+24 h]", "Unknown evidence excluded from primary analysis"], ORANGE),
        (1450, 160, 2200, 480, "Prespecified model contrasts", ["M0: 5-feature adapted benchmark", "M1: 29-feature snapshot CatBoost", "M2: 140-feature dynamic CatBoost", "M3: 140-feature Elastic Net", "M1 and M2 algorithm/budget matched"], BLUE),
        (1450, 560, 2200, 940, "Held-out evaluation", ["Untouched outer-fold probabilities", "AUPRC primary; AUROC co-key", "Brier, log loss, calibration", "Decision curves and alert burden", "1,000 patient/hospital cluster bootstraps"], TEAL),
        (740, 1040, 2200, 1320, "Locked interpretation", ["Key sensitivity analyses: interface completeness, strict/Unknown phenotype bounds,", "first landmark, timing proxy, and death/discharge competing-event filters", "Conclusion boundary: incremental predictive information; not deployment readiness"], RED),
    ]
    for x1, y1, x2, y2, header, lines, color in boxes:
        d.rounded_rectangle((x1, y1, x2, y2), radius=24, fill="#F8FAFC", outline=color, width=5)
        d.rectangle((x1, y1, x2, y1 + 70), fill=color)
        d.text((x1 + 28, y1 + 14), header, font=F(35, True), fill=WHITE)
        yy = y1 + 96
        for line in lines:
            d.ellipse((x1 + 30, yy + 12, x1 + 43, yy + 25), fill=color)
            d.text((x1 + 62, yy), line, font=F(27), fill=INK)
            yy += 48
    draw_arrow(d, (590, 340, 740, 470), BLUE)
    draw_arrow(d, (590, 710, 740, 590), TEAL)
    draw_arrow(d, (1300, 530, 1450, 320), ORANGE)
    draw_arrow(d, (1300, 570, 1450, 740), ORANGE)
    draw_arrow(d, (1825, 940, 1825, 1040), GRAY)
    d.text((90, 1360), "IECV = internal–external cross-validation; IMV = invasive mechanical ventilation.", font=F(24), fill=GRAY)
    im.save(OUT / "V2_FIGURE1_WORKFLOW_V0_1.png", dpi=(300, 300))

    parts = [svg_text(90, 92, title, 54, weight=700)]
    for x1, y1, x2, y2, header, lines, color in boxes:
        parts.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="24" fill="#F8FAFC" stroke="{color}" stroke-width="5"/>')
        parts.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="70" rx="20" fill="{color}"/>')
        parts.append(svg_text(x1 + 28, y1 + 48, header, 35, weight=700, fill=WHITE))
        yy = y1 + 122
        for line in lines:
            parts.append(f'<circle cx="{x1+37}" cy="{yy-8}" r="7" fill="{color}"/>')
            parts.append(svg_text(x1 + 62, yy, line, 27))
            yy += 48
    parts.extend([
        arrow_svg(590, 340, 740, 470, BLUE), arrow_svg(590, 710, 740, 590, TEAL),
        arrow_svg(1300, 530, 1450, 320, ORANGE), arrow_svg(1300, 570, 1450, 740, ORANGE),
        arrow_svg(1825, 940, 1825, 1040, GRAY),
        svg_text(90, 1380, "IECV = internal–external cross-validation; IMV = invasive mechanical ventilation.", 24, fill=GRAY),
    ])
    write_svg(OUT / "V2_FIGURE1_WORKFLOW_V0_1.svg", width, height, "\n".join(parts))


def pick(rows: list[dict[str, str]], **conditions: str) -> dict[str, str]:
    for row in rows:
        if all(row.get(k) == v for k, v in conditions.items()):
            return row
    raise KeyError(conditions)


def figure2() -> None:
    primary_m = pick(read_csv(RESULTS / "t4/mimic_M2_VS_M1_CLUSTER_BOOTSTRAP_CI_V0_1.csv"), metric="auprc")
    primary_e = pick(read_csv(RESULTS / "t4/eicu_M2_VS_M1_CLUSTER_BOOTSTRAP_CI_V0_1.csv"), metric="auprc")
    first_m = pick(read_csv(RESULTS / "t6_sensitivity/mimic_FIRST_LANDMARK_CLUSTER_BOOTSTRAP_CI_V0_1.csv"), reference="M1_SNAPSHOT_CAT", candidate="M2_DYNAMIC_CAT", metric="auprc")
    first_e = pick(read_csv(RESULTS / "t6_sensitivity/eicu_FIRST_LANDMARK_CLUSTER_BOOTSTRAP_CI_V0_1.csv"), reference="M1_SNAPSHOT_CAT", candidate="M2_DYNAMIC_CAT", metric="auprc")
    strict_m = read_csv(RESULTS / "t6_sensitivity/mimic_STRICT_UNKNOWN_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv")
    strict_e = read_csv(RESULTS / "t6_sensitivity/eicu_STRICT_UNKNOWN_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv")
    comp_m = read_csv(RESULTS / "t6_sensitivity/mimic_COMPETING_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv")
    comp_e = read_csv(RESULTS / "t6_sensitivity/eicu_COMPETING_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv")
    interface = pick(read_csv(RESULTS / "t6_sensitivity/eicu_INTERFACE_COMPLETE_M2_VS_M1_BOOTSTRAP_CI_V0_1.csv"), metric="auprc")

    def triple(row: dict[str, str]) -> tuple[float, float, float]:
        return float(row["candidate_minus_reference"] if "candidate_minus_reference" in row else row["m2_minus_m1"]), float(row["difference_ci_low"]), float(row["difference_ci_high"])

    rows = [
        ("MIMIC-IV", "Primary rolling task", triple(primary_m), BLUE),
        ("", "First landmark only", triple(first_m), BLUE),
        ("", "Unknown as negative", triple(pick(strict_m, scenario="MIMIC_UNKNOWN_AS_NEGATIVE", metric="auprc")), BLUE),
        ("", "Unknown as positive", triple(pick(strict_m, scenario="MIMIC_UNKNOWN_AS_POSITIVE", metric="auprc")), BLUE),
        ("", "Any competing event censored", triple(pick(comp_m, scenario="ANY_COMPETING_CENSORED", metric="auprc")), BLUE),
        ("eICU", "Primary rolling task", triple(primary_e), TEAL),
        ("", "First landmark only", triple(first_e), TEAL),
        ("", "Interface-complete hospitals", triple(interface), TEAL),
        ("", "Strict A/B vs None", triple(pick(strict_e, scenario="EICU_STRICT_AB_VS_NONE", metric="auprc")), TEAL),
        ("", "Unknown as negative", triple(pick(strict_e, scenario="EICU_UNKNOWN_AS_NEGATIVE", metric="auprc")), TEAL),
        ("", "Unknown as positive", triple(pick(strict_e, scenario="EICU_UNKNOWN_AS_POSITIVE", metric="auprc")), TEAL),
        ("", "Any competing event censored", triple(pick(comp_e, scenario="ANY_COMPETING_CENSORED", metric="auprc")), TEAL),
    ]

    width, height = 2400, 1680
    left, right = 900, 1640
    xmin, xmax = -0.006, 0.024
    top, step = 235, 103

    def xp(value: float) -> int:
        return int(left + (value - xmin) / (xmax - xmin) * (right - left))

    im = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(im)
    d.text((80, 50), "Figure 2. Incremental AUPRC of dynamic versus snapshot CatBoost", font=F(50, True), fill=INK)
    d.text((80, 120), "Points are M2−M1 differences; bars are 95% cluster-bootstrap confidence intervals.", font=F(28), fill=GRAY)
    for tick in [-0.005, 0, 0.005, 0.010, 0.015, 0.020]:
        x = xp(tick)
        d.line((x, top - 25, x, top + step * (len(rows) - 1) + 52), fill="#D5DDE5", width=2)
        d.text((x, top + step * len(rows) + 18), f"{tick:+.3f}", font=F(25), anchor="ma", fill=GRAY)
    d.line((xp(0), top - 35, xp(0), top + step * (len(rows) - 1) + 52), fill=INK, width=4)
    svg = [svg_text(80, 90, "Figure 2. Incremental AUPRC of dynamic versus snapshot CatBoost", 50, weight=700),
           svg_text(80, 150, "Points are M2−M1 differences; bars are 95% cluster-bootstrap confidence intervals.", 28, fill=GRAY)]
    for tick in [-0.005, 0, 0.005, 0.010, 0.015, 0.020]:
        x = xp(tick)
        svg.append(f'<line x1="{x}" y1="{top-25}" x2="{x}" y2="{top+step*(len(rows)-1)+52}" stroke="#D5DDE5" stroke-width="2"/>')
        svg.append(svg_text(x, top + step * len(rows) + 45, f"{tick:+.3f}", 25, anchor="middle", fill=GRAY))
    svg.append(f'<line x1="{xp(0)}" y1="{top-35}" x2="{xp(0)}" y2="{top+step*(len(rows)-1)+52}" stroke="{INK}" stroke-width="4"/>')
    for idx, (database, label, values, color) in enumerate(rows):
        y = top + idx * step
        est, lo, hi = values
        if database:
            d.text((80, y - 20), database, font=F(30, True), fill=color)
            svg.append(svg_text(80, y + 10, database, 30, weight=700, fill=color))
        d.text((270, y - 20), label, font=F(28), fill=INK)
        d.line((xp(lo), y, xp(hi), y), fill=color, width=8)
        d.ellipse((xp(est) - 12, y - 12, xp(est) + 12, y + 12), fill=color)
        d.text((1680, y - 19), f"{est:+.4f} ({lo:+.4f}, {hi:+.4f})", font=F(24), fill=INK)
        svg.extend([
            svg_text(270, y + 10, label, 28),
            f'<line x1="{xp(lo)}" y1="{y}" x2="{xp(hi)}" y2="{y}" stroke="{color}" stroke-width="8"/>',
            f'<circle cx="{xp(est)}" cy="{y}" r="12" fill="{color}"/>',
            svg_text(1680, y + 8, f"{est:+.4f} ({lo:+.4f}, {hi:+.4f})", 24),
        ])
    footer_y = top + step * len(rows) + 96
    d.text(((left + right) // 2, footer_y), "ΔAUPRC (M2 dynamic − M1 snapshot)", font=F(30, True), anchor="ma", fill=INK)
    d.text((80, 1610), "Positive values favour M2. Complete-case competing-event estimates apply to selected conditional risk sets.", font=F(25), fill=GRAY)
    svg.extend([
        svg_text((left + right) / 2, footer_y + 10, "ΔAUPRC (M2 dynamic − M1 snapshot)", 30, weight=700, anchor="middle"),
        svg_text(80, 1635, "Positive values favour M2. Complete-case competing-event estimates apply to selected conditional risk sets.", 25, fill=GRAY),
    ])
    im.save(OUT / "V2_FIGURE2_FOREST_AUPRC_V0_1.png", dpi=(300, 300))
    write_svg(OUT / "V2_FIGURE2_FOREST_AUPRC_V0_1.svg", width, height, "\n".join(svg))


def figure3() -> None:
    nested = pick(read_csv(RESULTS / "t4/eicu_RAW_CALIBRATION_SUMMARY_V0_1.csv"), model_id="M2_DYNAMIC_CAT")
    recal = read_csv(RESULTS / "t5_transport/rolling_MIMIC_TO_EICU_M2_DYNAMIC_CAT_CROSSFIT_RECALIBRATION_SUMMARY_V0_1.csv")
    dca = [row for row in read_csv(RESULTS / "t4/eicu_DCA_ALERT_BURDEN_V0_1.csv") if row["model_id"] == "M2_DYNAMIC_CAT"]
    oe = [
        ("eICU nested OOF", float(nested["oe_ratio"]), TEAL),
        ("Raw MIMIC→eICU", float(pick(recal, update_type="raw")["oe_ratio"]), RED),
        ("Intercept update", float(pick(recal, update_type="intercept_only")["oe_ratio"]), ORANGE),
        ("Intercept+slope update", float(pick(recal, update_type="intercept_slope")["oe_ratio"]), BLUE),
    ]
    dca = sorted(dca, key=lambda r: float(r["threshold"]))

    width, height = 2400, 1450
    im = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(im)
    d.text((80, 45), "Figure 3. Calibration transport and eICU alert-burden trade-off", font=F(50, True), fill=INK)
    d.text((90, 140), "A", font=F(42, True), fill=INK)
    d.text((160, 140), "Observed-to-expected event ratio", font=F(36, True), fill=INK)
    d.text((1260, 140), "B", font=F(42, True), fill=INK)
    d.text((1330, 140), "Threshold-dependent operating burden", font=F(36, True), fill=INK)
    svg = [svg_text(80, 82, "Figure 3. Calibration transport and eICU alert-burden trade-off", 50, weight=700),
           svg_text(90, 165, "A", 42, weight=700), svg_text(160, 165, "Observed-to-expected event ratio", 36, weight=700),
           svg_text(1260, 165, "B", 42, weight=700), svg_text(1330, 165, "Threshold-dependent operating burden", 36, weight=700)]

    # Panel A: O/E horizontal bars, with 1.0 reference.
    ax_l, ax_r, ax_top, ax_bot = 360, 1120, 260, 1120
    max_oe = 1.2
    def xoe(v: float) -> int:
        return int(ax_l + v / max_oe * (ax_r - ax_l))
    d.line((xoe(1), ax_top, xoe(1), ax_bot), fill=INK, width=4)
    svg.append(f'<line x1="{xoe(1)}" y1="{ax_top}" x2="{xoe(1)}" y2="{ax_bot}" stroke="{INK}" stroke-width="4"/>')
    for tick in [0, .2, .4, .6, .8, 1.0, 1.2]:
        x = xoe(tick)
        d.line((x, ax_bot, x, ax_bot + 10), fill=INK, width=2)
        d.text((x, ax_bot + 22), f"{tick:.1f}", font=F(23), anchor="ma", fill=GRAY)
        svg.append(svg_text(x, ax_bot + 50, f"{tick:.1f}", 23, anchor="middle", fill=GRAY))
    for i, (label, value, color) in enumerate(oe):
        y = 360 + i * 180
        d.text((110, y + 24), label, font=F(28), fill=INK)
        d.rounded_rectangle((ax_l, y, xoe(value), y + 72), radius=16, fill=color)
        d.text((xoe(value) + 18, y + 18), f"{value:.3f}", font=F(27, True), fill=color)
        svg.extend([
            svg_text(110, y + 50, label, 28),
            f'<rect x="{ax_l}" y="{y}" width="{max(1, xoe(value)-ax_l)}" height="72" rx="16" fill="{color}"/>',
            svg_text(xoe(value) + 18, y + 50, f"{value:.3f}", 27, weight=700, fill=color),
        ])
    d.text((560, 1230), "O/E ratio (1.0 = calibration-in-the-large)", font=F(27, True), anchor="ma", fill=INK)
    svg.append(svg_text(740, 1260, "O/E ratio (1.0 = calibration-in-the-large)", 27, weight=700, anchor="middle"))

    # Panel B: sensitivity and alerts per detected event as paired bars.
    bx_l, bx_r = 1320, 2250
    y0, dy = 310, 170
    for i, row in enumerate(dca):
        y = y0 + i * dy
        threshold = 100 * float(row["threshold"])
        sens = float(row["sensitivity"])
        burden = float(row["alerts_per_detected_event"])
        d.text((1240, y + 32), f"{threshold:g}%", font=F(29, True), fill=INK)
        # Scale sensitivity to 0-1 on a 390 px track; burden to 0-100 on same track.
        d.rectangle((bx_l, y, bx_l + 390, y + 46), fill=LIGHT)
        d.rectangle((bx_l, y, bx_l + int(390 * sens), y + 46), fill=TEAL)
        d.text((bx_l + 410, y + 5), f"Sensitivity {sens:.3f}", font=F(24), fill=TEAL)
        d.rectangle((bx_l, y + 68, bx_l + 390, y + 114), fill=LIGHT)
        d.rectangle((bx_l, y + 68, bx_l + int(390 * min(burden, 100) / 100), y + 114), fill=ORANGE)
        d.text((bx_l + 410, y + 73), f"{burden:.1f} alerts/event", font=F(24), fill=ORANGE)
        svg.extend([
            svg_text(1240, y + 34, f"{threshold:g}%", 29, weight=700),
            f'<rect x="{bx_l}" y="{y}" width="390" height="46" fill="{LIGHT}"/>',
            f'<rect x="{bx_l}" y="{y}" width="{int(390*sens)}" height="46" fill="{TEAL}"/>',
            svg_text(bx_l + 410, y + 32, f"Sensitivity {sens:.3f}", 24, fill=TEAL),
            f'<rect x="{bx_l}" y="{y+68}" width="390" height="46" fill="{LIGHT}"/>',
            f'<rect x="{bx_l}" y="{y+68}" width="{int(390*min(burden,100)/100)}" height="46" fill="{ORANGE}"/>',
            svg_text(bx_l + 410, y + 100, f"{burden:.1f} alerts/event", 24, fill=ORANGE),
        ])
    d.text((1235, 1230), "Risk threshold", font=F(27, True), fill=INK)
    d.text((90, 1370), "Panel A: raw cross-database transport markedly overpredicted risk; cross-fit updating restored average calibration but did not improve AUROC.", font=F(25), fill=GRAY)
    d.text((90, 1405), "Panel B: operating points were prespecified; no clinical workload threshold was selected from these results.", font=F(25), fill=GRAY)
    svg.extend([
        svg_text(1235, 1260, "Risk threshold", 27, weight=700),
        svg_text(90, 1390, "Panel A: raw cross-database transport markedly overpredicted risk; cross-fit updating restored average calibration but did not improve AUROC.", 25, fill=GRAY),
        svg_text(90, 1425, "Panel B: operating points were prespecified; no clinical workload threshold was selected from these results.", 25, fill=GRAY),
    ])
    im.save(OUT / "V2_FIGURE3_CALIBRATION_ALERT_BURDEN_V0_1.png", dpi=(300, 300))
    write_svg(OUT / "V2_FIGURE3_CALIBRATION_ALERT_BURDEN_V0_1.svg", width, height, "\n".join(svg))


if __name__ == "__main__":
    figure1()
    figure2()
    figure3()
    for output in sorted(OUT.glob("V2_FIGURE*V0_1.*")):
        print(output.relative_to(ROOT), output.stat().st_size)

"""Generate PDF briefing reports with clickable hyperlinks.

Takes a plain-text briefing (morning or BAS) and produces a formatted PDF
with clickable Google Maps links for fishing locations, source URLs, and
other references.

Usage:
    cd apps/workflow && uv run python pdf_report.py morning
    cd apps/workflow && uv run python pdf_report.py bas
    cd apps/workflow && uv run python pdf_report.py --input /path/to/file.md --output /path/to/out.pdf
"""

import re
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from fpdf import FPDF

app = typer.Typer(help="Generate PDF briefing with clickable hyperlinks")

BRIEFINGS_DIR = Path("C:/Users/AustinKC91/briefings")

# ── Known locations → Google Maps coordinates ──────────────────────────
# Lake Texoma spots
LAKE_TEXOMA_LOCATIONS = {
    "Willis Bridge": "33.8857,-96.8292",          # SH-99/US-377 bridge at Willis, OK
    "Roosevelt Bridge": "34.0012,-96.6125",        # US-70 bridge crossing
    "Denison Dam": "33.8175,-96.5700",             # latitude.to verified
    "Big Mineral": "33.8500,-96.7883",             # TCEQ water quality station
    "Big Mineral arm": "33.8500,-96.7883",         # Big Mineral arm of lake
    "Catfish Bay": "33.9680,-96.6399",             # Catfish Bay, Kingston OK side
    "Preston Peninsula": "33.8801,-96.6488",       # Preston Bend on the peninsula
    "Burns Run": "33.8573,-96.5775",               # Burns Run area (midpoint E/W)
    "West Burns Run": "33.8633,-96.5802",          # Recreation.gov verified
    "East Burns Run": "33.8514,-96.5747",          # Recreation.gov verified
    "Buncombe Creek": "33.8967,-96.8103",          # Recreation.gov verified
    "Little Mineral": "33.8732,-96.6484",          # Little Mineral Marina
    "Eisenhower State Park": "33.8146,-96.6095",   # TPWD verified
    "Soldier Creek": "33.9994,-96.7196",           # Soldier Creek Marina, Kingston OK
    "Cedar Bayou": "33.8430,-96.8525",             # Cedar Bayou Marina, Gordonville TX
    "Grandpappy Point": "33.8530,-96.6429",        # Grandpappy Point Marina
    "Mill Creek": "33.8190,-96.7735",              # Mill Creek Marina, Pottsboro TX
    "Rock Creek": "33.8864,-96.6870",              # Rock Creek Resort, Gordonville TX
    "Highport": "33.8249,-96.7099",                # Highport Marina, Pottsboro TX
    "Highport Marina": "33.8249,-96.7099",         # PredictWind verified
    "Alberta Creek": "33.9542,-96.6030",           # Alberta Creek Marina, Kingston OK
    "Platter Flats": "33.9216,-96.5477",           # Recreation.gov verified
    "Oakland": "34.1001,-96.7939",                 # Oakland, Marshall County OK
    "North Island": "33.8350,-96.7250",            # The Islands area near Highport
    "Washita River": "33.9170,-96.5830",           # Washita-Red River confluence
    "Red River": "33.8500,-96.6000",               # Red River channel mid-lake
    "Willow Springs Marina": "33.9751,-96.5719",   # Willow Springs, Mead OK
    "Lake Texoma": "33.8258,-96.5693",             # General lake reference
}

# Offshore / coastal locations
OFFSHORE_LOCATIONS = {
    "Corpus Christi": "27.8006,-97.3964",
    "Port Aransas": "27.8339,-97.0611",
    "Destin": "30.3935,-86.4958",
    "Destin FL": "30.3935,-86.4958",
    "Denison TX": "33.7557,-96.5367",
    "Denison": "33.7557,-96.5367",
    "Giga Texas": "30.2222,-97.6164",
    "Austin TX": "30.2672,-97.7431",
}

# Arkansas White River locations (verified March 2026)
ARKANSAS_LOCATIONS = {
    "Rim Shoals": "36.2580,-92.4744",               # Natural Atlas verified
    "Bull Shoals Dam": "36.3661,-92.5751",           # Mapcarta/damsoftheworld verified
    "Bull Shoals State Park": "36.3606,-92.5744",    # Arkansas State Parks verified
    "Wildcat Shoals": "36.3081,-92.5742",            # Natural Atlas verified
    "Norfork Tailwater": "36.2583,-92.2406",         # Recreation.gov verified
    "Quarry Park": "36.2583,-92.2406",               # Recreation.gov Dam-Quarry
    "Dry Run Creek": "36.2583,-92.2406",             # At Quarry Park / Norfork Hatchery
    "Cotter": "36.2712,-92.5354",                    # latlong.net town center
    "Cotter Trout Dock": "36.2659,-92.5434",         # Yelp/YellowPages verified
    "Calico Rock": "36.1181,-92.1350",               # Encyclopedia of Arkansas verified
    "Flys and Guides": "36.3454,-92.5831",           # LoopNet parcel 469 River Rd
    "Dally's Ozark Fly Fisher": "36.2857,-92.5139",  # Yelp/Yahoo verified
    "Wishes & Fishes": "36.3735,-92.5847",           # Bull Shoals Central Blvd
    "Bull Shoals Lake Boat Dock": "36.3818,-92.5958", # Fishing.org/Camping.org verified
    "Woolum": "35.9704,-92.8870",                    # CampingRoadTrip.com verified
    "Gunner Pool": "35.9944,-92.2123",               # Recreation.gov verified
    "Bull Shoals": "36.3735,-92.5847",               # latlong.net city center
    "Mountain Home": "36.3354,-92.3854",             # City center
    "Lakeview": "36.3696,-92.5447",                  # City center
    "Gassville": "36.2832,-92.4940",                 # City center
    "Flippin": "36.2788,-92.5970",                   # City center
    # Little Red River / Greers Ferry locations (verified March 2026)
    "JFK Park": "35.5132,-91.9969",                     # Recreation.gov campground 232613
    "Greers Ferry Dam": "35.5200,-92.0000",             # Dam location
    "Greers Ferry Lake": "35.5200,-91.9500",            # Lake center
    "Cow Shoals": "35.5134,-91.9300",                   # Natural Atlas verified
    "Libby Shoals": "35.4573,-91.9485",                 # Natural Atlas verified
    "Winkley Shoals": "35.4897,-91.9717",               # Natural Atlas verified
    "Barnett Access": "35.4897,-91.9717",               # Same as Winkley Shoals
    "Heber Springs": "35.4917,-91.9979",                # City center
    "Little Red River": "35.5000,-91.9500",             # General river reference
    # Millwood Lake locations
    "Millwood Lake": "33.6946,-93.9609",                # Lake center / White Cliffs
    "Millwood State Park": "33.6774,-93.9874",          # CampingRoadTrip.com verified
    "White Cliffs Park": "33.6946,-93.9609",            # Recreation.gov campground 250013
    "Ashdown": "33.6743,-94.1313",                      # City center
    # DeGray Lake locations
    "DeGray Lake": "34.2536,-93.1323",                  # CampingRoadTrip.com verified
    "DeGray Lake Resort": "34.2536,-93.1323",           # Arkansas State Parks
    "Arkadelphia": "34.1209,-93.0538",                  # City center
    # Wapanocca NWR
    "Wapanocca": "35.3664,-90.2520",                    # Recreation.gov gateway 1663
    "Wapanocca NWR": "35.3664,-90.2520",                # USFWS
    "Turrell": "35.3584,-90.2598",                      # City center
    # Memphis
    "Memphis": "35.1495,-90.0490",                      # City center
    "Memphis TN": "35.1495,-90.0490",                   # City center
    # Heber Springs restaurants/shops
    "The Ozark Angler": "35.4920,-91.9690",             # 659 Wilburn Rd
    "Red River Trout Dock": "35.4700,-91.9650",         # 285 Ferguson Rd
    "Rouse Fly Fishing": "35.4917,-91.9979",            # 470 Wildflower Rd
    "Zeke & Lizzy's": "35.4890,-91.9980",              # 404 S 7th St
    "Mack's Fish House": "35.4920,-91.9690",            # 559 Wilburn Rd
    "Verona Italian": "35.4820,-91.9980",               # 1220 S 7th St
    "Cafe Klaser": "35.4967,-91.9874",                  # 1414 Wilburn Rd - Restaurant.com verified
    "ColdWater Grill": "35.4911,-91.9739",              # 35 Swinging Bridge Dr
    "Lobo Access": "35.4567,-91.9315",                  # 3525 Libby Rd
    # Spring River / Mammoth Spring locations (verified March 2026)
    "Mammoth Spring State Park": "36.4958,-91.5350",    # GPS Basecamp verified
    "Mammoth Spring": "36.4958,-91.5350",               # State park / Dam 1
    "Lassiter Access": "36.4884,-91.5350",              # Waze verified
    "Cold Springs Access": "36.4780,-91.5330",          # Executive Angler / USGS verified
    "Dam 3 Access": "36.4662,-91.5280",                 # Paddling.com verified
    "Spring River": "36.4800,-91.5300",                 # General river reference
    "Hardy": "36.3159,-91.4826",                        # TopoZone verified
    "AR Welcome Center": "36.4963,-91.5356",            # Campendium verified
    "McCormack Lake": "36.8217,-91.3526",               # TopoZone verified
    "Cane Bluff": "36.7963,-91.4058",                   # TheDyrt verified
    "Spring River Flies and Guides": "36.4694,-91.5042", # FindAGrave/Yelp verified
    "Spring River Draft House": "36.3178,-91.4888",     # Wheree.com verified
    "The Bluff Steakhouse": "36.3268,-91.4968",         # Arkansas.com verified
    "Henry Gray Hurricane Lake WMA": "35.2324,-91.4830", # TheDyrt verified
}

ALL_LOCATIONS = {**LAKE_TEXOMA_LOCATIONS, **OFFSHORE_LOCATIONS, **ARKANSAS_LOCATIONS}

# ── Source URLs for known sources ──────────────────────────────────────
SOURCE_URLS = {
    "TPWD": "https://tpwd.texas.gov/fishing/sea-center-texas/fishing-report",
    "ODWC": "https://www.wildlifedepartment.com/fishing/reports",
    "USACE": "https://www.swt-wc.usace.army.mil/DENI.lakepage.html",
    "SPC": "https://www.spc.noaa.gov/products/outlook/",
    "NWS Fort Worth": "https://forecast.weather.gov/MapClick.php?CityName=Denison&state=TX",
    "wttr.in": "https://wttr.in/Denison+TX",
    "SeaTemperature": "https://www.seatemperature.org/",
    "Water Data for Texas": "https://www.waterdatafortexas.org/reservoirs/individual/texoma",
    "StriperExpress": "https://www.striperexpress.com/",
    "Texoma Connect": "https://texomaconnect.com/",
    "Captain Experiences": "https://www.captainexperiences.com/",
    "FishingBooker": "https://fishingbooker.com/",
    "NOAA": "https://www.weather.gov/marine/",
    "TechCrunch": "https://techcrunch.com/",
    "Bloomberg": "https://www.bloomberg.com/",
    "NWTF": "https://www.nwtf.org/",
}


def _maps_url(coords: str) -> str:
    """Google Maps URL from lat,lng coordinates."""
    return f"https://www.google.com/maps?q={coords}"


class BriefingPDF(FPDF):
    """Custom PDF with header/footer and hyperlink support."""

    def __init__(self, title: str = "Morning Briefing"):
        super().__init__()
        self.report_title = title
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, self.report_title, align="L")
        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), self.w - 10, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_header(self, text: str):
        """Bold section header with colored background."""
        self.ln(3)
        self.set_fill_color(30, 60, 120)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 9, f"  {text}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def sub_header(self, text: str):
        """Bold sub-section header."""
        self.ln(2)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(30, 60, 120)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def body_text(self, text: str):
        """Regular body text."""
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def linked_text(self, label: str, url: str, prefix: str = "", suffix: str = ""):
        """Text with a clickable hyperlink."""
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 30, 30)
        if prefix:
            self.write(5, prefix)
        self.set_text_color(0, 80, 180)
        self.set_font("Helvetica", "U", 9.5)
        self.write(5, label, url)
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 30, 30)
        if suffix:
            self.write(5, suffix)

    def location_link(self, name: str, coords: str, context: str = ""):
        """Clickable map pin for a location."""
        url = _maps_url(coords)
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 30, 30)
        if context:
            self.write(5, context)
        self.set_text_color(0, 120, 60)
        self.set_font("Helvetica", "BU", 9.5)
        self.write(5, f"[Map: {name}]", url)
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 30, 30)

    def source_link(self, name: str, url: str):
        """Clickable source reference."""
        self.set_text_color(0, 80, 180)
        self.set_font("Helvetica", "U", 9)
        self.write(5, name, url)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)


def _inject_gps_links(pdf: BriefingPDF, text: str):
    """Write text with GPS coordinate patterns turned into clickable Google Maps links.

    Matches patterns like '36.2580, -92.4744' or '36.2580,-92.4744' and makes them
    clickable links to Google Maps.
    """
    # Match GPS coordinates: lat, lng (with optional spaces around comma)
    gps_pattern = re.compile(r"(-?\d{1,3}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})")

    last_end = 0
    for match in gps_pattern.finditer(text):
        # Write text before this match
        before = text[last_end:match.start()]
        if before:
            pdf.write(5, before)

        # Write the GPS coordinates as a clickable link
        lat, lng = match.group(1), match.group(2)
        coords_text = match.group(0)
        url = _maps_url(f"{lat},{lng}")
        pdf.set_text_color(0, 120, 60)
        pdf.set_font("Helvetica", "BU", 9.5)
        pdf.write(5, coords_text, url)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(30, 30, 30)

        last_end = match.end()

    # Write remaining text
    remaining = text[last_end:]
    if remaining:
        pdf.write(5, remaining)

    return last_end > 0  # True if any GPS coords were found


def _inject_location_links(pdf: BriefingPDF, text: str):
    """Write text with location names and GPS coordinates turned into clickable map links."""
    # Sort locations by length (longest first) to avoid partial matches
    sorted_locs = sorted(ALL_LOCATIONS.keys(), key=len, reverse=True)

    # Build a regex that matches any known location name
    pattern_parts = [re.escape(loc) for loc in sorted_locs]
    loc_pattern = re.compile(r"\b(" + "|".join(pattern_parts) + r")\b", re.IGNORECASE)

    segments = loc_pattern.split(text)
    if len(segments) == 1:
        # No known location name found - still check for GPS coordinates
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(30, 30, 30)
        if _inject_gps_links(pdf, text):
            pdf.ln(3)
        else:
            # Reset X position before multi_cell to avoid "not enough space" errors
            pdf.set_x(pdf.l_margin)
            pdf.body_text(text)
        return

    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(30, 30, 30)

    for seg in segments:
        # Check if this segment is a location name
        matched_loc = None
        for loc in sorted_locs:
            if seg.lower() == loc.lower():
                matched_loc = loc
                break

        if matched_loc:
            coords = ALL_LOCATIONS[matched_loc]
            url = _maps_url(coords)
            pdf.set_text_color(0, 120, 60)
            pdf.set_font("Helvetica", "BU", 9.5)
            pdf.write(5, seg, url)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(30, 30, 30)
        else:
            # Check for GPS coordinates within this text segment
            _inject_gps_links(pdf, seg)

    pdf.ln(3)


def _inject_source_links(pdf: BriefingPDF, text: str):
    """Write a sources line with known sources hyperlinked."""
    sorted_sources = sorted(SOURCE_URLS.keys(), key=len, reverse=True)
    pattern_parts = [re.escape(s) for s in sorted_sources]
    pattern = re.compile(r"(" + "|".join(pattern_parts) + r")", re.IGNORECASE)

    segments = pattern.split(text)

    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(80, 80, 80)

    for seg in segments:
        matched = None
        for src in sorted_sources:
            if seg.lower() == src.lower():
                matched = src
                break
        if matched:
            url = SOURCE_URLS[matched]
            pdf.set_text_color(0, 80, 180)
            pdf.set_font("Helvetica", "IU", 8.5)
            pdf.write(5, seg, url)
            pdf.set_font("Helvetica", "I", 8.5)
            pdf.set_text_color(80, 80, 80)
        else:
            pdf.write(5, seg)

    pdf.ln(3)
    pdf.set_text_color(30, 30, 30)


def _parse_sections(text: str) -> list[tuple[str, str]]:
    """Split briefing text into (header, body) sections."""
    lines = text.strip().split("\n")
    sections = []
    current_header = ""
    current_body_lines = []

    for line in lines:
        stripped = line.strip()
        # Detect section headers: lines that are ALL CAPS and at least 5 chars
        # Also detect "---" separators
        if stripped == "---":
            if current_header or current_body_lines:
                sections.append((current_header, "\n".join(current_body_lines).strip()))
                current_header = ""
                current_body_lines = []
            continue

        # Check if line is a section header (ALL CAPS, 5+ chars, no lowercase)
        if (
            len(stripped) >= 5
            and stripped == stripped.upper()
            and re.search(r"[A-Z]", stripped)
            and not stripped.startswith("- ")
            and ":" not in stripped[:20]  # Allow colons later in line
            or (stripped.startswith("GOOD MORNING") or stripped.startswith("GOOD EVENING"))
        ):
            if current_header or current_body_lines:
                sections.append((current_header, "\n".join(current_body_lines).strip()))
            current_header = stripped
            current_body_lines = []
        else:
            current_body_lines.append(line)

    if current_header or current_body_lines:
        sections.append((current_header, "\n".join(current_body_lines).strip()))

    return sections


def _is_sub_header(line: str) -> bool:
    """Detect sub-headers like 'STRIPER REPORT:', 'CATFISH:', etc."""
    stripped = line.strip()
    if not stripped:
        return False
    # Lines that are ALL CAPS ending with ':'
    if stripped.endswith(":") and stripped == stripped.upper() and len(stripped) >= 4:
        return True
    # Lines that are ALL CAPS and short (sub-section names)
    if stripped == stripped.upper() and len(stripped) <= 40 and re.search(r"[A-Z]{3,}", stripped):
        if not stripped.startswith("- ") and not stripped.startswith("SSW") and not stripped.startswith("NWS"):
            return True
    return False


def _is_sources_line(line: str) -> bool:
    return line.strip().lower().startswith("sources:")


def generate_pdf(input_text: str, output_path: str, title: str = "Morning Briefing"):
    """Generate a formatted PDF with hyperlinks from briefing text."""
    pdf = BriefingPDF(title=title)
    pdf.alias_nb_pages()
    pdf.add_page()

    sections = _parse_sections(input_text)

    for header, body in sections:
        if not header and not body:
            continue

        # Title line (GOOD MORNING AUSTIN!)
        if header and ("GOOD MORNING" in header or "GOOD EVENING" in header):
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(30, 60, 120)
            pdf.cell(0, 12, header, new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)
            if body:
                pdf.body_text(body)
            continue

        # Section header
        if header:
            pdf.section_header(header)

        if not body:
            continue

        # Process body line by line
        body_lines = body.split("\n")
        i = 0
        while i < len(body_lines):
            line = body_lines[i]
            stripped = line.strip()

            if not stripped:
                pdf.ln(2)
                i += 1
                continue

            # Sources line - add hyperlinks to sources
            if _is_sources_line(stripped):
                _inject_source_links(pdf, stripped)
                i += 1
                continue

            # Sub-headers
            if _is_sub_header(stripped):
                pdf.sub_header(stripped)
                i += 1
                continue

            # Bullet points
            if stripped.startswith("- "):
                _inject_location_links(pdf, stripped)
                i += 1
                continue

            # Day-by-day lines (kiteboarding, weather)
            if re.match(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Today|Tonight|Tomorrow|Rest of week)", stripped):
                _inject_location_links(pdf, stripped)
                i += 1
                continue

            # Regular paragraph - collect consecutive non-empty, non-header lines
            para_lines = [stripped]
            i += 1
            while i < len(body_lines):
                next_line = body_lines[i].strip()
                if (
                    not next_line
                    or _is_sub_header(next_line)
                    or _is_sources_line(next_line)
                    or next_line.startswith("- ")
                ):
                    break
                para_lines.append(next_line)
                i += 1

            paragraph = " ".join(para_lines)
            _inject_location_links(pdf, paragraph)
            continue

        # This shouldn't be reached, but just in case
        i += 1 if i < len(body_lines) else 0

    # Final footer note
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5, "Green underlined text = clickable map links  |  Blue underlined text = clickable source links", align="C")

    pdf.output(output_path)
    return output_path


@app.command("morning")
def morning(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output PDF path"),
):
    """Generate morning briefing PDF with hyperlinks."""
    input_path = BRIEFINGS_DIR / "morning-latest.md"
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        raise typer.Exit(1)

    text = input_path.read_text(encoding="utf-8")
    out = output or str(BRIEFINGS_DIR / "morning-briefing.pdf")
    generate_pdf(text, out, title="AustBot Morning Briefing")
    print(f"PDF generated: {out}")


@app.command("bas")
def bas(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output PDF path"),
):
    """Generate BAS intel report PDF with hyperlinks."""
    input_path = BRIEFINGS_DIR / "bas-latest.md"
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        raise typer.Exit(1)

    text = input_path.read_text(encoding="utf-8")
    out = output or str(BRIEFINGS_DIR / "bas-report.pdf")
    generate_pdf(text, out, title="AustBot BAS Intel Report")
    print(f"PDF generated: {out}")


@app.command("custom")
def custom(
    input_file: str = typer.Argument(..., help="Input text/markdown file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output PDF path"),
    title: str = typer.Option("Briefing Report", "--title", "-t", help="PDF title"),
):
    """Generate PDF from any text file with hyperlinks."""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        raise typer.Exit(1)

    text = input_path.read_text(encoding="utf-8")
    out = output or str(input_path.with_suffix(".pdf"))
    generate_pdf(text, out, title=title)
    print(f"PDF generated: {out}")


if __name__ == "__main__":
    app()

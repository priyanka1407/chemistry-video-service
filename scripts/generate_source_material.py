"""Generates source_material/chemistry_source.pdf -- the single PDF that is
the retrieval corpus for the 3 supported topics.

This is the "source of truth" the retrieve-then-generate pipeline (app/rag)
indexes: script generation is only allowed to draw on text that appears in
here, and the faithfulness judge (app/judge/grounding.py) checks every
generated claim against chunks retrieved from this exact file. Regenerate
with:

    python scripts/generate_source_material.py

Each section starts with a `## <topic_id>` marker (parsed by
app/rag/loader.py to split the PDF into per-topic passages before chunking)
-- do not remove or rename these markers without updating the loader's
SECTION_MARKERS.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT_PATH = Path(__file__).resolve().parent.parent / "source_material" / "chemistry_source.pdf"

# Each tuple is (topic_id, heading, [paragraphs]). topic_id MUST match the
# ids in app/topics.py -- that's the join key between the PDF and the app.
SECTIONS: list[tuple[str, str, list[str]]] = [
    (
        "ph_scale",
        "How does the pH scale work?",
        [
            "The pH scale measures how acidic or basic (alkaline) an aqueous solution is. "
            "It is defined as the negative base-10 logarithm of the hydrogen ion "
            "concentration: pH = -log10[H+]. The scale conventionally runs from 0 to 14, "
            "although values outside that range are possible for very strong acids or bases.",

            "A solution with a pH below 7 is acidic, meaning it has a higher concentration "
            "of free hydrogen ions (H+) than pure water. A solution with a pH of exactly 7, "
            "such as pure water at 25 degrees Celsius, is neutral. A solution with a pH "
            "above 7 is basic, or alkaline, meaning it has a lower concentration of free "
            "hydrogen ions and a correspondingly higher concentration of hydroxide ions (OH-).",

            "Because the scale is logarithmic rather than linear, each single whole-number "
            "step represents a tenfold change in hydrogen ion concentration. A solution at "
            "pH 4 is ten times more acidic than a solution at pH 5, and one hundred times "
            "more acidic than a solution at pH 6. This is why small differences in a "
            "measured pH value can correspond to large differences in actual acidity.",

            "Common examples help calibrate the scale: lemon juice has a pH around 2 "
            "(strongly acidic), black coffee is around 5 (mildly acidic), pure water is 7 "
            "(neutral), baking soda solution is around 9 (mildly basic), and household "
            "bleach or soap solutions can reach pH 12-13 (strongly basic).",

            "pH is typically measured using a calibrated electronic pH meter (a glass "
            "electrode sensitive to hydrogen ion activity) or, less precisely, with "
            "colorimetric indicators such as litmus paper or universal indicator solution, "
            "which change color across the pH range.",
        ],
    ),
    (
        "covalent_bond_formation",
        "Why do atoms form covalent bonds?",
        [
            "Atoms form covalent bonds because doing so lowers the total potential energy "
            "of the system, moving each atom's outer (valence) electron shell toward a more "
            "stable, lower-energy configuration -- for most main-group elements, a full "
            "outer shell of eight electrons (the octet rule), or two for hydrogen and helium.",

            "A covalent bond forms when two atoms -- typically two nonmetals with similar "
            "electronegativity -- each contribute one electron to a shared pair. That shared "
            "pair of electrons occupies the region between the two nuclei and is "
            "simultaneously attracted to both positively charged nuclei, which is the "
            "electrostatic force that holds the atoms together.",

            "This is different from an atom simply losing or gaining an electron outright: "
            "in covalent bonding neither atom fully gives up its electron. Instead, the "
            "electrons are shared, which lets both atoms count the shared pair toward their "
            "own outer shell simultaneously, satisfying both atoms' drive toward a full "
            "shell without requiring a net charge transfer.",

            "The number of covalent bonds an atom tends to form is governed by how many "
            "additional electrons it needs to complete its outer shell. For example, a "
            "hydrogen atom needs one more electron and typically forms one bond; an oxygen "
            "atom needs two more electrons and typically forms two bonds, as in water (H2O).",

            "The result of covalent bonding is a molecule: a discrete, electrically neutral "
            "unit in which specific atoms are held together by shared electron pairs, with a "
            "fixed, well-defined composition (for example, exactly two hydrogen atoms and "
            "one oxygen atom in every water molecule).",
        ],
    ),
    (
        "ionic_vs_covalent",
        "What is the difference between ionic and covalent bonding?",
        [
            "Ionic and covalent bonds are the two principal ways atoms combine to form "
            "compounds, and they differ in what happens to the electrons involved. In ionic "
            "bonding, one or more electrons are transferred completely from one atom to "
            "another. In covalent bonding, electrons are shared between atoms rather than "
            "transferred.",

            "The deciding factor is usually the electronegativity difference between the two "
            "atoms -- how strongly each atom's nucleus attracts shared electrons. A large "
            "electronegativity difference, typically between a metal (which readily gives up "
            "electrons) and a nonmetal (which readily accepts them), favors ionic bonding. A "
            "small electronegativity difference, typically between two nonmetals, favors "
            "covalent bonding.",

            "In ionic bonding, the atom that loses an electron becomes a positively charged "
            "ion (a cation), and the atom that gains it becomes a negatively charged ion (an "
            "anion). These oppositely charged ions attract each other electrostatically. "
            "Sodium chloride (table salt, NaCl) is a classic example: sodium transfers an "
            "electron to chlorine, forming Na+ and Cl- ions.",

            "The resulting structures also differ. Ionic compounds do not form individual "
            "molecules; instead, the oppositely charged ions arrange themselves into a "
            "repeating, three-dimensional crystal lattice held together by electrostatic "
            "attraction throughout the whole structure. Covalent compounds form discrete "
            "molecules, each with a fixed, specific number of atoms bonded by shared "
            "electron pairs.",

            "These structural differences produce different bulk properties. Ionic compounds "
            "tend to have high melting points and conduct electricity when dissolved in "
            "water or melted, because the charged ions become free to move. Covalent "
            "compounds tend to have lower melting points and generally do not conduct "
            "electricity, because their molecules are electrically neutral overall.",
        ],
    ),
]


def build_pdf() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SectionTitle", parent=styles["Heading1"], alignment=TA_LEFT, spaceAfter=12,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["BodyText"], alignment=TA_LEFT, spaceAfter=10, leading=16,
    )
    marker_style = ParagraphStyle(
        "Marker", parent=styles["Normal"], textColor="white", fontSize=1,
    )

    doc = SimpleDocTemplate(
        str(OUT_PATH),
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title="Chemistry Source Material",
        author="Chemistry Video Request Service",
    )

    story = [
        Paragraph("Chemistry Source Material", styles["Title"]),
        Paragraph(
            "Reference material for the 3 supported concepts. This is the sole source "
            "the video-generation pipeline is allowed to draw on -- every generated "
            "script is retrieved and grounded against the paragraphs below, and every "
            "factual claim in a generated script is checked back against this text "
            "before a video is approved for delivery.",
            body_style,
        ),
        Spacer(1, 0.2 * inch),
    ]

    for topic_id, heading, paragraphs in SECTIONS:
        # Machine-readable section marker, parsed by app/rag/loader.py. Kept
        # visually invisible (tiny white text) so the PDF still reads cleanly
        # for a human while remaining reliably machine-parseable.
        story.append(Paragraph(f"## {topic_id}", marker_style))
        story.append(Paragraph(heading, title_style))
        for para in paragraphs:
            story.append(Paragraph(para, body_style))
        story.append(Spacer(1, 0.3 * inch))

    doc.build(story)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    build_pdf()

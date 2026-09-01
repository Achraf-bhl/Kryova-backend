"""Licence tiers, configurations and the product trigram table.

Licensing decides answer *correctness* here, not just cost. A command that does
not exist on the user's seat is not a command they can be told to use, and the
symptom of a missing licence -- a greyed icon, a workbench absent from the Start
menu -- looks exactly like the symptom of being in the wrong place. So every
workbench and most commands in this package carry a `licence` field, and this
module is where the codes in those fields are resolved.

**The trigram table is transcribed from the IBM/Dassault V5R19 Configurations
and Products Portfolio**, which is the authoritative published mapping. Codes
that a user will name but that are not in that catalogue -- community shorthand
like `WSF` for Wireframe & Surface, or `AMT` for Advanced Meshing Tools -- are
listed separately and marked as informal, because telling someone their licence
is `WSF` when their licence manager says `WS1` sends them looking for something
that is not there.

The tier suffix on a product name is the platform: `1` is P1, `2` is P2, `3` is
P3. `Assembly Design 1` (AS1) and `Assembly Design 2` (ASD) are the same
workbench with different depth, which is why a user on P1 can be looking at a
dialog with fewer options than the manual shows.
"""

from __future__ import annotations

from typing import Final, Mapping

from app.catia_kb.types import Kind, Section, bulk, entry

#: Product code to product name, from the V5R19 portfolio. CATIA, DMU, Real
#: Time Rendering and CAA products only -- the ENOVIA and DELMIA long tail is
#: not something a CATIA user names when asking why a command is greyed out.
TRIGRAMS: Final[Mapping[str, str]] = {
    "ABF": "CATIA Automotive Body in White Fastening 3",
    "ABT": "CATIA Automotive Body in White Templates 2",
    "AMG": "CATIA Advanced Machining 2",
    "ANR": "DMU Engineering Analysis Review 2",
    "AS1": "CATIA Assembly Design 1",
    "ASD": "CATIA Assembly Design 2",
    "ASL": "CATIA Aerospace Sheetmetal Design 3",
    "BK2": "CATIA Business Process Knowledge Template 2",
    "BKT": "CATIA Business Process Knowledge Template 3",
    "C12": "CATIA COM 1 to 2 Extension",
    "CBD": "CATIA Circuit Board Design 1",
    "CC1": "CATIA CADAM Interface 1",
    "CCD": "CATIA CADAM Drafting for V5 Products",
    "CCV": "CATIA Core & Cavity Design 2",
    "CD1": "CATIA Instant Collaborative Design 1",
    "CDG": "CAA – C++ API Documentation Generator",
    "CFO": "CATIA Cast & Forged Part Optimizer 2",
    "CID": "CAA – C++ Interactive Dashboard",
    "CNA": "CATIA Compartment & Access 2",
    "CO1": "CATIA Object Manager 1",
    "CO3": "CATIA Object Manager 3",
    "COM": "CATIA Object Manager 2",
    "CPD": "CATIA Composites Design 3",
    "CPE": "CATIA Composites Engineering 2",
    "CPM": "CATIA Composites Design for Manufacturing 2",
    "CPR": "DMU Composites Review 2",
    "CSC": "CAA – C++ Source Checker",
    "CUT": "CAA – C++ Unit Test Manager",
    "DF1": "CATIA Product Data Filtering 1",
    "DL1": "CATIA Developed Shapes 1",
    "DMC": "CAA – DataModel Customizer",
    "DMN": "CATIA DMU Navigator 2",
    "DMO": "DMU Optimizer 2",
    "DN1": "CATIA DMU Navigator 1",
    "DSE": "CATIA Digitized Shape Editor 2",
    "DR1": "CATIA Drafting 1 configuration",
    "HAA": "Human Activity Analysis 2",
    "HBR": "Human Builder 2",
    "HME": "Human Measurements Editor 2",
    "HPA": "Human Posture Analysis 2",
    "PX1": "PPR PDM Gateway 1",
    "DSS": "CATIA Shape Sculptor 2",
    "DT1": "DMU Dimensioning & Tolerancing Review 1",
    "EAS": "CATIA User Companion for Extended Structural Analysis Product",
    "EC1": "CATIA Electrical 3D Design & Documentation 1",
    "ECR": "CATIA Electrical Cableway Routing 2",
    "EFD": "CATIA Electrical System Functional Definition 2",
    "EHF": "CATIA Electrical Harness Flattening 2",
    "EHI": "CATIA Electrical Harness installation 2",
    "ELB": "CATIA Electrical Library 2",
    "ELD": "CATIA Electrical Connectivity Diagrams 2",
    "EQT": "CATIA Equipment Arrangement 2",
    "EST": "CATIA Elfini Structural Analysis 2",
    "EW1": "CATIA ENOVIAVPM Supply Chain Engineering Exchange 1",
    "EWE": "CATIA ENOVIAVPM Supply Chain Engineering Exchange 2",
    "EWR": "CATIA Electrical Wire Routing 2",
    "FAR": "DMU Fastening Review 2",
    "FIT": "DMU Fitting Simulator 2",
    "FLX": "CATIA Flex Physical Simulation 2",
    "FM1": "CATIA Functional Molded Part 1",
    "FMD": "CATIA FEM Solid 2",
    "FMP": "CATIA Functional Molded Parts 2",
    "FMS": "CATIA FEM Surface 2",
    "FR1": "CATIA Part Design Feature Recognition 1",
    "FS1": "CATIA Freestyle Shaper 1",
    "FSK": "CATIA FreeStyle Sketch Tracer 1",
    "FSO": "CATIA FreeStyle Optimizer 2",
    "FSP": "CATIA FreeStyle Profiler 2",
    "FSS": "CATIA FreeStyle Shaper 2",
    "FT1": "CATIA 3D Functional Tolerancing & Annotation 1",
    "FTA": "CATIA 3D Functional Tolerancing & Annotation 2",
    "GAS": "CATIA Generative Assembly Structural Analysis 2",
    "GD1": "CATIA Generative Drafting 1",
    "GDR": "CATIA Generative Drafting 2",
    "GDY": "CATIA Generative Dynamic Response Analysis 2",
    "GP1": "CATIA Generative Part Structural Analysis 1",
    "GPS": "CATIA Generative Part Structural Analysis 2",
    "GS1": "CATIA Generative Shape Design 1",
    "GSD": "CATIA Generative Shape Design 2",
    "GSO": "CATIA Generative Shape Optimizer 2",
    "HA1": "CATIA Healing Assistant 1",
    "HDS": "CATIA User Companion for Hybrid Design Product",
    "HGR": "CATIA Hanger Design 2",
    "HVA": "CATIA HVAC Design 2",
    "HVD": "CATIA HVAC Diagrams 2",
    "ID1": "CATIA Interactive Drafting 1",
    "IG1": "CATIA IGES Interface 1",
    "IMA": "CATIA Imagine & Shape 2",
    "ITC": "CAA – Interactive Test Capture",
    "JID": "CAA – Java Interactive Dashboard",
    "JUT": "CAA – Java Unit Test Manager",
    "KE1": "CATIA Knowledge Expert 1",
    "KIN": "DMU Kinematics Simulator 2",
    "KT1": "CATIA Product Knowledge Template 1",
    "KWA": "CATIA Knowledge Advisor 2",
    "KWE": "CATIA Knowledge Expert 2",
    "LG1": "CATIA Lathe Machining 1",
    "LMG": "CATIA Lathe Machining 2",
    "LO1": "CATIA 2D Layout for 3D Design 1",
    "M4S": "CATIA User Companion for V4 Mechanical Design",
    "MAB": "CAA – Multi-Workspace Application Builder",
    "MBG": "CATIA NC Machine Tool Builder 2",
    "MDS": "CATIA User Companion for Mechanical Design",
    "MLG": "CATIA Multi-Slide Lathe Machining 2",
    "MMG": "CATIA Multi-Axis Surface Machining 2",
    "MPA": "CATIA Prismatic Machining Preparation Assistant 2",
    "MPG": "CATIA Multi-Pocket Machining 2",
    "MSG": "CATIA NC Machine Tool Simulation 2",
    "MTD": "CATIA Mold Tooling Design 2",
    "NCG": "CATIA NC Manufacturing Review 2",
    "NG1": "CATIA NC Manufacturing Review 1",
    "NVG": "CATIA NC Manufacturing Verification 2",
    "PD1": "CATIA Part Design 1",
    "PDG": "CATIA Part Design 2",
    "PEO": "CATIA Product Engineering Optimizer 2",
    "PFD": "CATIA Product Function Definition 2",
    "PG1": "CATIA Prismatic Machining 1",
    "PID": "CATIA Piping & Instrumentation Diagrams 2",
    "PIP": "CATIA Piping Design 2",
    "PKT": "CATIA Product Knowledge Template 2",
    "PLO": "CATIA Plant Layout 1",
    "PMG": "CATIA Prismatic Machining 2",
    "QSR": "CATIA Quick Surface Reconstruction 2",
    "RCD": "CATIA Raceway & Conduit Design 2",
    "RSO": "CATIA Realistic Shape Optimizer 2",
    "RT1": "Real Time Rendering 1",
    "RTR": "Real Time Rendering 2",
    "SAS": "CATIA User Companion for Structural Analysis Product",
    "SCM": "CAA – Source Code Manager",
    "SDD": "CATIA Ship Structure Detail Design 2",
    "SDI": "CATIA Systems Diagrams 2",
    "SFD": "CATIA Structure Functional Design 2",
    "SH1": "CATIA Sheetmetal Production 1",
    "SM1": "CATIA Sheetmetal Design 1",
    "SMD": "CATIA Sheetmetal Design 2",
    "SMG": "CATIA 3 Axis Surface Machining 2",
    "SMS": "CATIA User Companion for Sheetmetal Product",
    "SP1": "DMU Space Analysis 1",
    "SPA": "DMU Space Analysis 2",
    "SPE": "CATIA DMU Space Engineering Assistant 2",
    "SR1": "CATIA Structure Design 1",
    "SRT": "CATIA Systems Routing 1",
    "SSR": "CATIA Systems Space Reservation 2",
    "ST1": "CATIA STEP Core Interface 1",
    "STC": "CATIA Strim/Styler to CATIA Interface 2",
    "STL": "CATIA STL Rapid Prototyping 2",
    "TAA": "CATIA Tolerance Analysis of Deformable Assembly 3",
    "TG1": "CATIA Tooling Design 1",
    "TL1": "CATIA STL Rapid Prototyping 1",
    "TUB": "CATIA Tubing Design 2",
    "TUD": "CATIA Tubing Diagrams 2",
    "V41": "CATIA V4 Integration 1",
    "V4I": "CATIA V4 Integration 2",
    "WAC": "CAA – Web Application Composer",
    "WAV": "CATIA Waveguide Design 2",
    "WD1": "CATIA Weld Design 1",
    "WGD": "CATIA Waveguide Diagrams 2",
    "WS1": "CATIA Wireframe & Surface 1",
}

#: Codes in common use that the R19 portfolio does not carry under that spelling.
#: Recognised, and reported as informal so nobody goes hunting for them in a
#: licence manager.
INFORMAL_TRIGRAMS: Final[Mapping[str, str]] = {
    "WSF": "Wireframe & Surface -- the catalogue code is WS1",
    "AMT": "Advanced Meshing Tools -- sold as part of the analysis line rather than under this code",
    "SKT": "Sketch Tracer -- the catalogue code is FSK (FreeStyle Sketch Tracer)",
    "DRD": "Drafting -- the catalogue codes are ID1 (Interactive), GD1/GDR (Generative)",
    "ANL": "Analysis, used loosely for the GPS/GAS/EST line",
    "ICM": "ICEM Shape Design -- a separate product line, not a V5 configuration code",
    "ESS": "Equipment Support Structures -- the catalogue code is EQT (Equipment Arrangement)",
    "PX2": "PPR PDM Gateway 2 -- PX1 is the catalogue code for gateway 1",
    "CNV": "Conversion/interface products, used loosely",
    "HAI": "Human ergonomics, used loosely for the HBR/HME/HPA/HAA line",
    "VPM": "ENOVIA VPM -- an ENOVIA product line, not a CATIA trigram",
    "SMG": "3 Axis Surface Machining 2 -- often called Surface Machining",
    "GSM": "Generative Sheetmetal Design -- the catalogue code is SMD",
}


def product(code: str) -> str | None:
    """Resolve a trigram to a product name, catalogue codes first."""
    key = (code or "").strip().upper()
    if key in TRIGRAMS:
        return TRIGRAMS[key]
    if key in INFORMAL_TRIGRAMS:
        return INFORMAL_TRIGRAMS[key]
    return None


_TIERS = [
    entry(
        "licence.tiers",
        "P1, P2 and P3 platforms",
        Kind.LICENCE,
        aliases=(
            "p1", "p2", "p3", "platform 1", "platform 2", "platform 3", "tier", "licence tier",
            "which platform", "what licence do i need", "plm express",
        ),
        summary="The three CATIA V5 platforms. A product exists at one or more of them, and the deeper tier has more commands in the same workbench.",
        fields=(
            "P1 -- entry platform, product codes ending in 1 (PD1, AS1, GD1, WS1, SM1). Same workbenches, fewer options",
            "P2 -- the mainstream engineering platform, codes without a numeric suffix or ending 2 (PDG, ASD, GSD, SMD, GDR)",
            "P3 -- specialist products, codes ending 3 (ASL, CPD, ABF, BKT). Aerospace Sheet Metal and Composites Design are both P3",
            "Configurations bundle products (MD1, MD2, HD2, MDG, AC1/AC2, SL1); add-on products (AOPs) are bought singly on top",
        ),
        failures=(
            "A P1 user follows a P2 manual and cannot find half the dialog options -- the command is there, the depth is not",
            "Aerospace Sheet Metal and Composites Design are P3: a site with an MD2 configuration does not have them at all",
        ),
        fixes=("Tools > Options > General > Licensing lists exactly what this seat holds; that is the answer, not the Start menu",),
        see_also=("setting.licensing", "diagnostic.command_greyed_out"),
    ),
]


_CONFIGURATIONS = bulk(
    """
MD1 | md1, mechanical design 1, mechanical design one
MD2 | md2, mechanical design 2, mechanical design two
ME1 | me1, mechanical engineering 1
ME2 | me2, mechanical engineering 2
HD2 | hd2, hybrid design 2, hybrid design
XM1 | xm1, extended mechanical design 1
XM2 | xm2, extended mechanical design 2
YM1 | ym1, styled mechanical design 1
DR1 | dr1, drafting 1 configuration
SD2 | sd2, sheetmetal design 2 configuration
CV2 | cv2, core and cavity design 2 configuration
MDG | mdg, mechanical design configuration
AC1 | ac1, aerospace configuration 1
AC2 | ac2, aerospace configuration 2
SL1 | sl1, structural design configuration
PLM Express | plm express, plmx, plm express configuration
""",
    kind=Kind.LICENCE,
    prefix="config",
    toolbar="Configurations",
)


def _trigram_entries():
    """One entry per catalogue code, so `PDG` and `ASL` are recognised terms."""
    out = []
    for code, name in TRIGRAMS.items():
        out.append(
            entry(
                f"trigram.{code.lower()}",
                code,
                Kind.LICENCE,
                aliases=(name,),
                summary=f"CATIA V5 product code for {name}.",
                licence=name,
            )
        )
    for code, note in INFORMAL_TRIGRAMS.items():
        if code in TRIGRAMS:
            continue
        out.append(
            entry(
                f"trigram.{code.lower()}",
                code,
                Kind.LICENCE,
                summary=f"Informal code: {note}.",
                licence=note,
            )
        )
    return out


ENTRIES = [*_TIERS, *_CONFIGURATIONS, *_trigram_entries()]

SECTION = Section("licensing", ENTRIES)

__all__ = ["ENTRIES", "INFORMAL_TRIGRAMS", "SECTION", "TRIGRAMS", "product"]

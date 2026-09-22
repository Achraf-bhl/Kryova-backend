"""ISO 286-1:2010 transcribed: a tolerance class becomes two deviations -- master plan 13.2.

`fits.py` says, in its own docstring, that **no deviation is shipped here** because "ISO
286's tables are adopted data an engineer chooses from rather than arithmetic", and that
reading them is "a document this codebase has not transcribed". That was true until the
document arrived. This module is the transcription, and `fits.py`'s sentence is now wrong
about this one thing and right about everything else: what a *fit* gives, and which fit to
choose, stay arithmetic over zones, and a zone may still arrive from a supplier's bearing
seat recommendation or a company standard instead of from here.

**What this module is.** Tables 1 to 5 of ISO 286-1:2010(E), plus the reading rules of
4.3.2 and the |delta|-value rule of 4.3.2.5, so that `zone(90, "F7")` answers +0,036/+0,071 the
way a person reading the standard would. Nothing is computed from a formula that the
standard prints as a table: the tables *are* the standard, and a re-derivation that agreed
to three digits and diverged on the fourth would be the worst possible outcome here.

**What it is not.** ISO 286-**2** -- the tables of limit deviations for the selected
classes -- is a separate document this repository does not have. It is not needed: 4.3.1
offers Tables 1 to 5 and ISO 286-2 as *alternative* routes to the same numbers, and 4.3.3's
worked example (60 M6, read from ISO 286-2's Table 9) is reproduced exactly by this module
from Part 1 alone. Where Part 2 would be cheaper is not correctness, it is confirmation.

## The four data tables, and the two rules that read them

*Table 1* is the standard tolerance value IT, by nominal size band and grade IT01 to IT18.
IT01 and IT0 are not defined above 500 mm, and this module refuses those rather than
extrapolating.

*Tables 2 and 3* are the **hole** fundamental deviations, A to M and N to ZC. *Tables 4 and
5* are the shafts. Only the hole tables are transcribed, because 4.3.2.3 and Figures 8/9
make the shaft value the exact negation of the hole value of the same letter -- and that was
checked cell by cell against the printed Tables 4 and 5 rather than assumed. See
`TestTheShaftTableIsTheHoleTableNegated` and the misprint recorded below.

*The other limit deviation* comes from Figures 8 and 9: a hole's `ES = EI + IT` where the
table gave EI (A to H), and `EI = ES - IT` where it gave ES (J to ZC). A shaft's
`ei = es - IT` and `es = ei + IT` the same way round. JS and js have no fundamental
deviation at all (NOTE 3 to 4.1) and are +/- IT/2.

*The* |delta| *rule*, 4.3.2.5, is the one that is easy to drop and impossible to notice
afterwards. For K, M and N up to and including IT8, and for P to ZC up to and including
IT7, the tabulated value is not the deviation -- a |delta| read from the right-hand columns of
Table 3 is added to it. 20 K7 is +0,006/-0,015 with the rule and -0,002/-0,023 without,
and both look like a tolerance. The rule exists so that a hole of grade n pairs with a
shaft of grade n-1 the way the fit tables promise, so dropping it silently breaks
precisely the fits it was written for.

## Three things in the printed tables that are not arithmetic

1. **A misprint, and which side of it is right.** Table 2 gives hole EF for 18 mm to 30 mm
   as **+28** and Table 4 gives shaft ef for the same band as **-25**. Every other cell of
   the two tables negates exactly -- checked for all of a to h across all 41 bands. ISO's own
   construction for the intermediate deviations is the geometric mean of its neighbours, and
   sqrt(e*f) = sqrt(40*20) = 28,28 reproduces EF in *every* band including this one, while 25
   matches nothing. So **+/-28 is transcribed and the -25 is treated as a misprint**, recorded
   here rather than silently corrected. It is the only disagreement in either table.
2. **N above IT8 is 0 -- except at 3 mm and below, where it is -4.** Table 3's "above IT8"
   column is 0 down the page and -4 in the first row. Not a rounding artefact: ISO 286-2
   gives N9 for 0 to 3 mm as -4/-29, and for 3 to 6 mm as 0/-30.
3. **M6 between 250 mm and 315 mm is -9, not the -11 the rule computes.** Footnote b to
   Table 2 says so in as many words. It is the only cell in the system that is stated as an
   exception to its own arithmetic.

## Refusals

A and B (and a and b) are refused at or below 1 mm, and N above IT8 likewise, because the
standard's footnotes say they "shall not be used" there -- not that they are undefined, which
is why the refusal names the footnote. A letter that simply has no column in a band (T below
24 mm, V below 14 mm, Y below 18 mm, CD/EF/FG above 50 mm, J and the a-to-c letters above
500 mm) is refused as absent. Every refusal names the size, the class and what to reach for
instead, because the caller is an engineer choosing a fit and the useful answer to "ZC is not
defined at 800 mm" is the range it *is* defined over.

Sizes run to 3 150 mm, which is where Table 1 stops.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Final

from app.rules.errors import RuleError
from app.rules.fits import Feature, ToleranceZone

#: Quoted wherever a zone built here records where its numbers came from. Every
#: `ToleranceZone` demands a source string and this is the one this module gives.
SOURCE: Final = "ISO 286-1:2010(E), Tables 1 to 5 and the reading rules of 4.3.2"

#: The largest nominal size Table 1 covers. Above it the standard says nothing and
#: neither does this module.
MAX_NOMINAL_MM: Final = 3150.0


class Iso286Error(RuleError):
    """A size, a tolerance class or a combination the standard does not cover."""


# --------------------------------------------------------------------------------------
# Table 1 -- standard tolerance values IT, in micrometres.
#
# The printed table gives IT01 to IT11 in micrometres and IT12 to IT18 in millimetres;
# both are stored here in micrometres, because a single unit is worth more than fidelity
# to a page's column headings. `None` is a grade the standard does not define in that
# band, which is IT01 and IT0 above 500 mm.
# --------------------------------------------------------------------------------------

#: Upper bound of each size band of Table 1, in mm. A band is "above the previous bound,
#: up to and including this one" -- so 30 mm is in the 18-to-30 band, not the 30-to-50 one.
IT_BANDS: Final[tuple[float, ...]] = (
    3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500,
    630, 800, 1000, 1250, 1600, 2000, 2500, 3150,
)

_N = None
IT_UM: Final[dict[str, tuple[float | None, ...]]] = {
    "01": (0.3, 0.4, 0.4, 0.5, 0.6, 0.6, 0.8, 1, 1.2, 2, 2.5, 3, 4, _N, _N, _N, _N, _N, _N, _N, _N),
    "0":  (0.5, 0.6, 0.6, 0.8, 1, 1, 1.2, 1.5, 2, 3, 4, 5, 6, _N, _N, _N, _N, _N, _N, _N, _N),
    "1":  (0.8, 1, 1, 1.2, 1.5, 1.5, 2, 2.5, 3.5, 4.5, 6, 7, 8, 9, 10, 11, 13, 15, 18, 22, 26),
    "2":  (1.2, 1.5, 1.5, 2, 2.5, 2.5, 3, 4, 5, 7, 8, 9, 10, 11, 13, 15, 18, 21, 25, 30, 36),
    "3":  (2, 2.5, 2.5, 3, 4, 4, 5, 6, 8, 10, 12, 13, 15, 16, 18, 21, 24, 29, 35, 41, 50),
    "4":  (3, 4, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 33, 39, 46, 55, 68),
    "5":  (4, 5, 6, 8, 9, 11, 13, 15, 18, 20, 23, 25, 27, 32, 36, 40, 47, 55, 65, 78, 96),
    "6":  (6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40, 44, 50, 56, 66, 78, 92, 110, 135),
    "7":  (10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63, 70, 80, 90, 105, 125, 150, 175, 210),
    "8":  (14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97, 110, 125, 140, 165, 195, 230, 280, 330),
    "9":  (25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155, 175, 200, 230, 260, 310, 370, 440, 540),
    "10": (40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250, 280, 320, 360, 420, 500, 600, 700, 860),
    "11": (60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400, 440, 500, 560, 660, 780, 920, 1100, 1350),
    "12": (100, 120, 150, 180, 210, 250, 300, 350, 400, 460, 520, 570, 630, 700, 800, 900, 1050, 1250, 1500, 1750, 2100),
    "13": (140, 180, 220, 270, 330, 390, 460, 540, 630, 720, 810, 890, 970, 1100, 1250, 1400, 1650, 1950, 2300, 2800, 3300),
    "14": (250, 300, 360, 430, 520, 620, 740, 870, 1000, 1150, 1300, 1400, 1550, 1750, 2000, 2300, 2600, 3100, 3700, 4400, 5400),
    "15": (400, 480, 580, 700, 840, 1000, 1200, 1400, 1600, 1850, 2100, 2300, 2500, 2800, 3200, 3600, 4200, 5000, 6000, 7000, 8600),
    "16": (600, 750, 900, 1100, 1300, 1600, 1900, 2200, 2500, 2900, 3200, 3600, 4000, 4400, 5000, 5600, 6600, 7800, 9200, 11000, 13500),
    "17": (1000, 1200, 1500, 1800, 2100, 2500, 3000, 3500, 4000, 4600, 5200, 5700, 6300, 7000, 8000, 9000, 10500, 12500, 15000, 17500, 21000),
    "18": (1400, 1800, 2200, 2700, 3300, 3900, 4600, 5400, 6300, 7200, 8100, 8900, 9700, 11000, 12500, 14000, 16500, 19500, 23000, 28000, 33000),
}

#: |delta| values, Table 3's right-hand columns, keyed by grade and indexed by `IT_BANDS`.
#: Only IT3 to IT8 have one, and only up to 500 mm -- above that the columns are blank
#: because K to ZC carry no |delta| correction there.
DELTA_UM: Final[dict[str, tuple[float, ...]]] = {
    "3": (0, 1, 1, 1, 1.5, 1.5, 2, 2, 3, 3, 4, 4, 5),
    "4": (0, 1.5, 1.5, 2, 2, 3, 3, 4, 4, 4, 4, 5, 5),
    "5": (0, 1, 2, 3, 3, 4, 5, 5, 6, 6, 7, 7, 7),
    "6": (0, 3, 3, 3, 4, 5, 6, 7, 7, 9, 9, 11, 13),
    "7": (0, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23),
    "8": (0, 6, 7, 9, 12, 14, 16, 19, 23, 26, 29, 32, 34),
}


# --------------------------------------------------------------------------------------
# Tables 2 and 3 -- hole fundamental deviations, in micrometres.
#
# Each letter is a run of `(upper_bound_mm, value)` pairs in ascending order, which is how
# the page reads: a merged cell spanning three size bands is one entry. A value applies
# from the previous entry's bound up to and including its own. `None` is a band the letter
# has no column in, and a letter simply stops where its column does.
# --------------------------------------------------------------------------------------

_Run = tuple[tuple[float, float | None], ...]

#: A to H: the table gives the **lower** limit deviation EI, and ES = EI + IT.
HOLE_EI_UM: Final[dict[str, _Run]] = {
    "A": ((3, 270), (6, 270), (10, 280), (18, 290), (30, 300), (40, 310), (50, 320),
          (65, 340), (80, 360), (100, 380), (120, 410), (140, 460), (160, 520), (180, 580),
          (200, 660), (225, 740), (250, 820), (280, 920), (315, 1050), (355, 1200),
          (400, 1350), (450, 1500), (500, 1650)),
    "B": ((3, 140), (6, 140), (10, 150), (18, 150), (30, 160), (40, 170), (50, 180),
          (65, 190), (80, 200), (100, 220), (120, 240), (140, 260), (160, 280), (180, 310),
          (200, 340), (225, 380), (250, 420), (280, 480), (315, 540), (355, 600),
          (400, 680), (450, 760), (500, 840)),
    "C": ((3, 60), (6, 70), (10, 80), (18, 95), (30, 110), (40, 120), (50, 130),
          (65, 140), (80, 150), (100, 170), (120, 180), (140, 200), (160, 210), (180, 230),
          (200, 240), (225, 260), (250, 280), (280, 300), (315, 330), (355, 360),
          (400, 400), (450, 440), (500, 480)),
    "CD": ((3, 34), (6, 46), (10, 56), (18, 70), (30, 85), (50, 100)),
    "D": ((3, 20), (6, 30), (10, 40), (18, 50), (30, 65), (50, 80), (80, 100), (120, 120),
          (180, 145), (250, 170), (315, 190), (400, 210), (500, 230), (630, 260),
          (800, 290), (1000, 320), (1250, 350), (1600, 390), (2000, 430), (2500, 480),
          (3150, 520)),
    "E": ((3, 14), (6, 20), (10, 25), (18, 32), (30, 40), (50, 50), (80, 60), (120, 72),
          (180, 85), (250, 100), (315, 110), (400, 125), (500, 135), (630, 145),
          (800, 160), (1000, 170), (1250, 195), (1600, 220), (2000, 240), (2500, 260),
          (3150, 290)),
    # 18 to 30 is +28 and not the -25 printed in Table 4 -- see the module docstring.
    "EF": ((3, 10), (6, 14), (10, 18), (18, 23), (30, 28), (50, 35)),
    "F": ((3, 6), (6, 10), (10, 13), (18, 16), (30, 20), (50, 25), (80, 30), (120, 36),
          (180, 43), (250, 50), (315, 56), (400, 62), (500, 68), (630, 76), (800, 80),
          (1000, 86), (1250, 98), (1600, 110), (2000, 120), (2500, 130), (3150, 145)),
    "FG": ((3, 4), (6, 6), (10, 8), (18, 10), (30, 12), (50, 15)),
    "G": ((3, 2), (6, 4), (10, 5), (18, 6), (30, 7), (50, 9), (80, 10), (120, 12),
          (180, 14), (250, 15), (315, 17), (400, 18), (500, 20), (630, 22), (800, 24),
          (1000, 26), (1250, 28), (1600, 30), (2000, 32), (2500, 34), (3150, 38)),
    "H": ((3150, 0),),
}

#: J, by grade: the table gives the **upper** limit deviation ES, and EI = ES - IT. J has
#: a column for IT6, IT7 and IT8 only, and none above 500 mm.
HOLE_J_ES_UM: Final[dict[str, _Run]] = {
    "6": ((3, 2), (6, 5), (10, 5), (18, 6), (30, 8), (50, 10), (80, 13), (120, 16),
          (180, 18), (250, 22), (315, 25), (400, 29), (500, 33)),
    "7": ((3, 4), (6, 6), (10, 8), (18, 10), (30, 12), (50, 14), (80, 18), (120, 22),
          (180, 26), (250, 30), (315, 36), (400, 39), (500, 43)),
    "8": ((3, 6), (6, 10), (10, 12), (18, 15), (30, 20), (50, 24), (80, 28), (120, 34),
          (180, 41), (250, 47), (315, 55), (400, 60), (500, 66)),
}

#: K to ZC: the table gives ES, and EI = ES - IT. These are the *base* values -- for K, M
#: and N up to IT8, and P to ZC up to IT7, a |delta| is added (4.3.2.5).
HOLE_ES_UM: Final[dict[str, _Run]] = {
    "K": ((3, 0), (6, -1), (10, -1), (18, -1), (30, -2), (50, -2), (80, -2), (120, -3),
          (180, -3), (250, -4), (315, -4), (400, -4), (500, -5), (3150, 0)),
    "M": ((3, -2), (6, -4), (10, -6), (18, -7), (30, -8), (50, -9), (80, -11), (120, -13),
          (180, -15), (250, -17), (315, -20), (400, -21), (500, -23), (630, -26),
          (800, -30), (1000, -34), (1250, -40), (1600, -48), (2000, -58), (2500, -68),
          (3150, -76)),
    "N": ((3, -4), (6, -8), (10, -10), (18, -12), (30, -15), (50, -17), (80, -20),
          (120, -23), (180, -27), (250, -31), (315, -34), (400, -37), (500, -40),
          (630, -44), (800, -50), (1000, -56), (1250, -66), (1600, -78), (2000, -92),
          (2500, -110), (3150, -135)),
    "P": ((3, -6), (6, -12), (10, -15), (18, -18), (30, -22), (50, -26), (80, -32),
          (120, -37), (180, -43), (250, -50), (315, -56), (400, -62), (500, -68),
          (630, -78), (800, -88), (1000, -100), (1250, -120), (1600, -140), (2000, -170),
          (2500, -195), (3150, -240)),
    "R": ((3, -10), (6, -15), (10, -19), (18, -23), (30, -28), (50, -34), (65, -41),
          (80, -43), (100, -51), (120, -54), (140, -63), (160, -65), (180, -68),
          (200, -77), (225, -80), (250, -84), (280, -94), (315, -98), (355, -108),
          (400, -114), (450, -126), (500, -132), (560, -150), (630, -155), (710, -175),
          (800, -185), (900, -210), (1000, -220), (1120, -250), (1250, -260), (1400, -300),
          (1600, -330), (1800, -370), (2000, -400), (2240, -440), (2500, -460),
          (2800, -550), (3150, -580)),
    "S": ((3, -14), (6, -19), (10, -23), (18, -28), (30, -35), (50, -43), (65, -53),
          (80, -59), (100, -71), (120, -79), (140, -92), (160, -100), (180, -108),
          (200, -122), (225, -130), (250, -140), (280, -158), (315, -170), (355, -190),
          (400, -208), (450, -232), (500, -252), (560, -280), (630, -310), (710, -340),
          (800, -380), (900, -430), (1000, -470), (1120, -520), (1250, -580), (1400, -640),
          (1600, -720), (1800, -820), (2000, -920), (2240, -1000), (2500, -1100),
          (2800, -1250), (3150, -1400)),
    "T": ((24, None), (30, -41), (40, -48), (50, -54), (65, -66), (80, -75), (100, -91),
          (120, -104), (140, -122), (160, -134), (180, -146), (200, -166), (225, -180),
          (250, -196), (280, -218), (315, -240), (355, -268), (400, -294), (450, -330),
          (500, -360), (560, -400), (630, -450), (710, -500), (800, -560), (900, -620),
          (1000, -680), (1120, -780), (1250, -840), (1400, -960), (1600, -1050),
          (1800, -1200), (2000, -1350), (2240, -1500), (2500, -1650), (2800, -1900),
          (3150, -2100)),
    "U": ((3, -18), (6, -23), (10, -28), (18, -33), (24, -41), (30, -48), (40, -60),
          (50, -70), (65, -87), (80, -102), (100, -124), (120, -144), (140, -170),
          (160, -190), (180, -210), (200, -236), (225, -258), (250, -284), (280, -315),
          (315, -350), (355, -390), (400, -435), (450, -490), (500, -540), (560, -600),
          (630, -660), (710, -740), (800, -840), (900, -940), (1000, -1050), (1120, -1150),
          (1250, -1300), (1400, -1450), (1600, -1600), (1800, -1850), (2000, -2000),
          (2240, -2300), (2500, -2500), (2800, -2900), (3150, -3200)),
    "V": ((14, None), (18, -39), (24, -47), (30, -55), (40, -68), (50, -81), (65, -102),
          (80, -120), (100, -146), (120, -172), (140, -202), (160, -228), (180, -252),
          (200, -284), (225, -310), (250, -340), (280, -385), (315, -425), (355, -475),
          (400, -530), (450, -595), (500, -660)),
    "X": ((3, -20), (6, -28), (10, -34), (14, -40), (18, -45), (24, -54), (30, -64),
          (40, -80), (50, -97), (65, -122), (80, -146), (100, -178), (120, -210),
          (140, -248), (160, -280), (180, -310), (200, -350), (225, -385), (250, -425),
          (280, -475), (315, -525), (355, -590), (400, -660), (450, -740), (500, -820)),
    "Y": ((18, None), (24, -63), (30, -75), (40, -94), (50, -114), (65, -144), (80, -174),
          (100, -214), (120, -254), (140, -300), (160, -340), (180, -380), (200, -425),
          (225, -470), (250, -520), (280, -580), (315, -650), (355, -730), (400, -820),
          (450, -920), (500, -1000)),
    "Z": ((3, -26), (6, -35), (10, -42), (14, -50), (18, -60), (24, -73), (30, -88),
          (40, -112), (50, -136), (65, -172), (80, -210), (100, -258), (120, -310),
          (140, -365), (160, -415), (180, -465), (200, -520), (225, -575), (250, -640),
          (280, -710), (315, -790), (355, -900), (400, -1000), (450, -1100), (500, -1250)),
    "ZA": ((3, -32), (6, -42), (10, -52), (14, -64), (18, -77), (24, -98), (30, -118),
           (40, -148), (50, -180), (65, -226), (80, -274), (100, -335), (120, -400),
           (140, -470), (160, -535), (180, -600), (200, -670), (225, -740), (250, -820),
           (280, -920), (315, -1000), (355, -1150), (400, -1300), (450, -1450),
           (500, -1600)),
    "ZB": ((3, -40), (6, -50), (10, -67), (14, -90), (18, -108), (24, -136), (30, -160),
           (40, -200), (50, -242), (65, -300), (80, -360), (100, -445), (120, -525),
           (140, -620), (160, -700), (180, -780), (200, -880), (225, -960), (250, -1050),
           (280, -1200), (315, -1300), (355, -1500), (400, -1650), (450, -1850),
           (500, -2100)),
    "ZC": ((3, -60), (6, -80), (10, -97), (14, -130), (18, -150), (24, -188), (30, -218),
           (40, -274), (50, -325), (65, -405), (80, -480), (100, -585), (120, -690),
           (140, -800), (160, -900), (180, -1000), (200, -1150), (225, -1250),
           (250, -1350), (280, -1550), (315, -1700), (355, -1900), (400, -2100),
           (450, -2400), (500, -2600)),
}

#: Shaft j, Table 4: the table gives the **lower** deviation ei, and es = ei + IT. It is
#: the one lower-case letter that is *not* the negation of its upper-case twin -- hole J is
#: grouped IT6/IT7/IT8 and shaft j is grouped IT5-and-IT6/IT7/IT8, so they are transcribed
#: separately. IT8 exists only at 3 mm and below.
SHAFT_J_EI_UM: Final[dict[str, _Run]] = {
    "5": ((3, -2), (6, -2), (10, -2), (18, -3), (30, -4), (50, -5), (80, -7), (120, -9),
          (180, -11), (250, -13), (315, -16), (400, -18), (500, -20)),
    "7": ((3, -4), (6, -4), (10, -5), (18, -6), (30, -8), (50, -10), (80, -12), (120, -15),
          (180, -18), (250, -21), (315, -26), (400, -28), (500, -32)),
    "8": ((3, -6),),
}
#: IT6 shares IT5's column, which is what "IT5 and IT6" in the printed header means.
SHAFT_J_EI_UM["6"] = SHAFT_J_EI_UM["5"]

#: Shaft k, Table 5: two columns, IT4-to-IT7 and "IT3 and above IT7". The second is zero
#: everywhere, and so is the first above 500 mm. Transcribed rather than negated from hole
#: K for the same reason as j -- the grade groupings differ.
SHAFT_K_EI_UM: Final[_Run] = (
    (3, 0), (6, 1), (10, 1), (18, 1), (30, 2), (50, 2), (80, 2), (120, 3), (180, 3),
    (250, 4), (315, 4), (400, 4), (500, 5), (3150, 0),
)

#: Letters whose deviation the tables give as the *upper* one for a hole. Everything else
#: A to H is given as the lower. The distinction decides which way Figures 8 and 9 apply.
_HOLE_ES_LETTERS: Final[frozenset[str]] = frozenset(HOLE_ES_UM) | {"J"}

#: Grades at or below which the |delta| of 4.3.2.5 is added, per letter.
_DELTA_CEILING: Final[dict[str, int]] = {"K": 8, "M": 8, "N": 8} | {
    letter: 7 for letter in ("P", "R", "S", "T", "U", "V", "X", "Y", "Z", "ZA", "ZB", "ZC")
}

_CLASS_RE: Final = re.compile(r"^([A-Za-z]{1,2})(01|0|[1-9]|1[0-8])$")


def _band_index(nominal_mm: float) -> int:
    """Which row of Table 1 a size falls in. Bands are open below, closed above."""
    for index, upper in enumerate(IT_BANDS):
        if nominal_mm <= upper:
            return index
    raise Iso286Error(
        f"{nominal_mm:g} mm is above the {MAX_NOMINAL_MM:g} mm where ISO 286-1's Table 1 "
        "stops. The ISO code system says nothing about sizes larger than that, so there is "
        "no tolerance to read; a size this large is toleranced by explicit deviations."
    )


def _read_run(run: _Run, nominal_mm: float) -> float | None:
    """The value a `(bound, value)` run gives at a size, or None where it gives none."""
    for upper, value in run:
        if nominal_mm <= upper:
            return None if value is None else float(value)
    return None


def standard_tolerance_um(nominal_mm: float, grade: str) -> float:
    """The IT value of Table 1, in micrometres.

    `grade` is the grade *number* as it is written in a class -- "7" for IT7, and "01" and
    "0" for the two finest, which are spelled that way and are not defined above 500 mm.
    """
    _check_size(nominal_mm)
    row = IT_UM.get(grade)
    if row is None:
        raise Iso286Error(
            f"IT{grade} is not a standard tolerance grade. ISO 286-1 defines IT01, IT0 and "
            "IT1 to IT18, and nothing else."
        )
    value = row[_band_index(nominal_mm)]
    if value is None:
        raise Iso286Error(
            f"IT{grade} is not defined at {nominal_mm:g} mm. Table 1 gives IT01 and IT0 only "
            "up to and including 500 mm; above that the finest grade in the system is IT1."
        )
    return float(value)


def delta_um(nominal_mm: float, grade: str) -> float:
    """The |delta| of Table 3's right-hand columns, in micrometres.

    Zero wherever the standard prints no |delta| -- outside IT3 to IT8, and above 500 mm --
    because |delta| is defined as a value *added* to a tabulated deviation, so its absence
    and a value of zero are the same instruction.
    """
    row = DELTA_UM.get(grade)
    if row is None:
        return 0.0
    index = _band_index(nominal_mm)
    return float(row[index]) if index < len(row) else 0.0


def _check_size(nominal_mm: float) -> None:
    if not math.isfinite(nominal_mm):
        raise Iso286Error("A nominal size must be a finite number of millimetres.")
    if nominal_mm <= 0.0:
        raise Iso286Error(f"A nominal size of {nominal_mm:g} mm is no feature of size.")


def parse_class(tolerance_class: str) -> tuple[str, str, Feature]:
    """Split "F7" into its letter, its grade number and whether it is a hole or a shaft.

    Case is the whole of the hole/shaft distinction in this system (4.2.1), so "h7" and
    "H7" are different tolerance classes and neither is a typo for the other.
    """
    text = tolerance_class.strip().replace(" ", "")
    match = _CLASS_RE.match(text)
    if match is None:
        raise Iso286Error(
            f"{tolerance_class!r} is not an ISO 286 tolerance class. A class is one or two "
            "letters and a grade number, upper case for a hole and lower case for a shaft "
            "-- H7, js9, ZC6."
        )
    letters, grade = match.group(1), match.group(2)
    if letters.isupper():
        feature = Feature.HOLE
    elif letters.islower():
        feature = Feature.SHAFT
    else:
        raise Iso286Error(
            f"{tolerance_class!r} mixes cases. A hole's identifier is upper case throughout "
            "and a shaft's lower case throughout -- ZC6 or zc6, never Zc6."
        )
    upper = letters.upper()
    if any(character in "ILOQW" for character in upper):
        raise Iso286Error(
            f"{tolerance_class!r} uses one of the letters ISO 286-1 leaves out. NOTE 1 to "
            "4.1 excludes I, L, O, Q and W (and their lower-case forms) to avoid confusion "
            "with numerals and with other symbols."
        )
    return upper, grade, feature


def limit_deviations_um(nominal_mm: float, tolerance_class: str) -> tuple[float, float]:
    """The two limit deviations of a toleranced size, in micrometres, lower first.

    This is 4.3.2 end to end: the grade gives IT from Table 1, the letter gives the
    fundamental deviation from Tables 2 to 5, the |delta| rule of 4.3.2.5 corrects it where it
    applies, and Figures 8 and 9 give the other deviation.
    """
    _check_size(nominal_mm)
    letter, grade, feature = parse_class(tolerance_class)
    it = standard_tolerance_um(nominal_mm, grade)

    if letter == "JS":
        # NOTE 3 to 4.1: JS and js have no fundamental deviation; the interval straddles
        # the nominal size. An odd IT in micrometres therefore gives a half-micrometre
        # deviation, which the standard permits and does not round.
        return (-it / 2.0, it / 2.0)

    _refuse_where_the_standard_says_not_to(nominal_mm, letter, grade, tolerance_class)
    fundamental = _fundamental_deviation_um(nominal_mm, letter, grade, feature, tolerance_class)

    if feature is Feature.HOLE:
        if letter in _HOLE_ES_LETTERS:
            return (fundamental - it, fundamental)  # ES tabulated, EI = ES - IT
        return (fundamental, fundamental + it)  # EI tabulated, ES = EI + IT
    if letter in _HOLE_ES_LETTERS:
        return (fundamental, fundamental + it)  # ei tabulated, es = ei + IT
    return (fundamental - it, fundamental)  # es tabulated, ei = es - IT


def _fundamental_deviation_um(
    nominal_mm: float, letter: str, grade: str, feature: Feature, spelled: str
) -> float:
    """One fundamental deviation from Tables 2 to 5, |delta| already applied.

    A shaft's value is the negation of the hole's of the same letter (4.3.2.3 with Figures
    8 and 9), except for j and k, which the standard groups by grade differently and which
    are therefore transcribed in their own right.
    """
    number = _grade_number(grade)

    if letter == "J":
        if feature is Feature.SHAFT:
            run = SHAFT_J_EI_UM.get(grade)
            value = _read_run(run, nominal_mm) if run is not None else None
            return _require(value, nominal_mm, spelled, "j", "IT5 to IT8, up to 500 mm")
        run = HOLE_J_ES_UM.get(grade)
        value = _read_run(run, nominal_mm) if run is not None else None
        return _require(value, nominal_mm, spelled, "J", "IT6, IT7 and IT8, up to 500 mm")

    if letter == "K" and feature is Feature.SHAFT:
        # Table 5's two columns: IT4 to IT7 takes the tabulated value, IT3 and anything
        # above IT7 is zero.
        if number is not None and 4 <= number <= 7:
            value = _read_run(SHAFT_K_EI_UM, nominal_mm)
            return _require(value, nominal_mm, spelled, "k", "every size up to 3 150 mm")
        return 0.0

    if letter in HOLE_ES_UM:
        base = _read_run(HOLE_ES_UM[letter], nominal_mm)
        base = _require(base, nominal_mm, spelled, letter, _where(letter))
        if feature is Feature.SHAFT:
            # 4.3.2.5's |delta| is a hole-side correction only; Table 5 carries no |delta|
            # columns, which is what makes an H-hole/shaft pair come out as the fit tables
            # promise.
            return -base
        if letter == "K" and number is not None and number > 8:
            # Table 2's "above IT8" column for K is blank in every row below 3 mm, and a
            # blank there is zero rather than "the same as the column beside it". Two
            # things say so: the 0-to-3 row prints "0 0" explicitly, and above IT8 the
            # general rule of 4.3.2.3 applies, which makes ES = -ei(k) -- and Table 5
            # gives shaft k as 0 for "IT3 and above IT7". The same reading of M gives -m,
            # which is exactly the value M's own "above IT8" column prints, so the rule
            # is confirmed on the letter where the table does not stay silent.
            return 0.0
        if letter == "N" and number is not None and number > 8:
            # Table 3's "above IT8" column: zero everywhere but the first band. N is the
            # one letter that departs from the general rule here -- -n would give -8 at
            # 3 to 6 mm and the table prints 0 -- so this is read off the page, not derived.
            return -4.0 if nominal_mm <= 3.0 else 0.0
        if letter == "M" and number == 6 and 250.0 < nominal_mm <= 315.0:
            return -9.0  # Footnote b to Table 2, stated as an exception to the arithmetic.
        ceiling = _DELTA_CEILING.get(letter)
        if ceiling is not None and number is not None and number <= ceiling:
            return base + delta_um(nominal_mm, grade)
        return base

    base = _read_run(HOLE_EI_UM[letter], nominal_mm) if letter in HOLE_EI_UM else None
    base = _require(base, nominal_mm, spelled, letter, _where(letter))
    return base if feature is Feature.HOLE else -base


def _grade_number(grade: str) -> int | None:
    """The grade as an integer, or None for IT01 and IT0, which order below IT1."""
    return None if grade in {"01", "0"} else int(grade)


def _where(letter: str) -> str:
    """The size range a letter's column actually covers, for a refusal to quote."""
    run = HOLE_ES_UM.get(letter) or HOLE_EI_UM.get(letter)
    if not run:
        return "no size range"
    first = next((index for index, (_, value) in enumerate(run) if value is not None), 0)
    start = run[first - 1][0] if first else 0.0
    return f"above {start:g} mm up to and including {run[-1][0]:g} mm"


def _require(
    value: float | None, nominal_mm: float, spelled: str, letter: str, where: str
) -> float:
    if value is None:
        raise Iso286Error(
            f"{spelled} is not in ISO 286-1: the tables give {letter} for {where}, and "
            f"{nominal_mm:g} mm is outside that. The standard leaves the cell empty rather "
            "than giving a value, so there is nothing to read and nothing to interpolate."
        )
    return float(value)


def _refuse_where_the_standard_says_not_to(
    nominal_mm: float, letter: str, grade: str, spelled: str
) -> None:
    """The two "shall not be used" footnotes, which are prohibitions and not gaps."""
    if letter in {"A", "B"} and nominal_mm <= 1.0:
        raise Iso286Error(
            f"{spelled} is refused at {nominal_mm:g} mm: footnote a to Tables 2 and 4 says "
            f"the fundamental deviations A and B shall not be used at or below 1 mm. The "
            "table does carry a value there; using it is what the standard forbids."
        )
    number = _grade_number(grade)
    if letter == "N" and number is not None and number > 8 and nominal_mm <= 1.0:
        raise Iso286Error(
            f"{spelled} is refused at {nominal_mm:g} mm: footnote b to Table 3 says N above "
            "IT8 shall not be used at or below 1 mm."
        )


def zone(nominal_mm: float, tolerance_class: str) -> ToleranceZone:
    """A `ToleranceZone` for `fits.py`, read from the standard rather than supplied.

    This is the join between this module and the fit arithmetic: everything in `fits.py`
    takes zones and asks what a pair gives, and until now every zone had to be handed in
    with the table it was read from. `zone(36, "H8")` is now one of those sources.
    """
    lower_um, upper_um = limit_deviations_um(nominal_mm, tolerance_class)
    _, _, feature = parse_class(tolerance_class)
    return ToleranceZone(
        designation=f"{nominal_mm:g} {tolerance_class.strip()}",
        feature=feature,
        nominal_mm=float(nominal_mm),
        upper_deviation_mm=upper_um / 1000.0,
        lower_deviation_mm=lower_um / 1000.0,
        source=SOURCE,
    )


def fit(nominal_mm: float, designation: str) -> "object":
    """A `Fit` from a designation such as "36 H8/f7" or just "H8/f7" at a given size.

    Returns `fits.Fit`, so the clearance, the kind and the span all come from the code
    that already answers those questions -- this module contributes the two zones and
    nothing else.
    """
    from app.rules.fits import Fit

    text = designation.strip()
    if "/" not in text:
        raise Iso286Error(
            f"{designation!r} is not a fit. A fit designation names both members, hole "
            "first, separated by a solidus -- H7/n6 (5.2.1)."
        )
    hole_class, shaft_class = (part.strip() for part in text.split("/", 1))
    hole_class = hole_class.rsplit(" ", 1)[-1]
    return Fit(hole=zone(nominal_mm, hole_class), shaft=zone(nominal_mm, shaft_class))


def deviations_mm(nominal_mm: float, tolerance_class: str) -> tuple[float, float]:
    """The two limit deviations in millimetres, lower first -- the units this codebase uses."""
    lower_um, upper_um = limit_deviations_um(nominal_mm, tolerance_class)
    return (lower_um / 1000.0, upper_um / 1000.0)


def limits_mm(nominal_mm: float, tolerance_class: str) -> tuple[float, float]:
    """The smallest and largest permitted sizes in millimetres."""
    lower, upper = deviations_mm(nominal_mm, tolerance_class)
    return (nominal_mm + lower, nominal_mm + upper)


def known_letters(feature: Feature) -> Sequence[str]:
    """Every fundamental deviation identifier the tables carry, in table order."""
    order = (
        "A", "B", "C", "CD", "D", "E", "EF", "F", "FG", "G", "H", "JS", "J",
        "K", "M", "N", "P", "R", "S", "T", "U", "V", "X", "Y", "Z", "ZA", "ZB", "ZC",
    )
    return tuple(name if feature is Feature.HOLE else name.lower() for name in order)


__all__ = [
    "DELTA_UM",
    "HOLE_EI_UM",
    "HOLE_ES_UM",
    "HOLE_J_ES_UM",
    "IT_BANDS",
    "IT_UM",
    "Iso286Error",
    "MAX_NOMINAL_MM",
    "SHAFT_J_EI_UM",
    "SHAFT_K_EI_UM",
    "SOURCE",
    "delta_um",
    "deviations_mm",
    "fit",
    "known_letters",
    "limit_deviations_um",
    "limits_mm",
    "parse_class",
    "standard_tolerance_um",
    "zone",
]

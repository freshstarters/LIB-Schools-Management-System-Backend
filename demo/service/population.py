"""Deterministic Gojo campus population. Emails are unique. Names may repeat."""

GIVEN_NAMES = (
    "Princess",
    "Emmanuel",
    "Comfort",
    "Prince",
    "Faith",
    "Daniel",
    "Esther",
    "Moses",
    "Helena",
    "Mary",
    "Sarah",
    "Joseph",
    "Peter",
    "Ruth",
    "Hannah",
    "David",
    "Abigail",
    "Isaiah",
    "Martha",
    "Rebecca",
    "Aaron",
    "Naomi",
    "Stephen",
    "Lydia",
    "Elizabeth",
    "Thomas",
    "Rachel",
    "Benjamin",
    "Musu",
    "Korto",
    "Satta",
    "Tenneh",
    "Hawa",
    "Fatu",
    "Yei",
    "Garmai",
    "Bendu",
    "Miatta",
    "Weedor",
)
SURNAMES = (
    "Flomo",
    "Morris",
    "Pewee",
    "Mulbah",
    "Tarnue",
    "Kroma",
    "Varney",
    "Dolo",
    "Kpoto",
    "Cooper",
    "Paye",
    "Massaquoi",
    "Konneh",
    "Sheriff",
    "Dukuly",
    "Kamara",
    "Sesay",
    "Koroma",
    "Jallah",
    "Gaye",
    "Wleh",
    "Nyumah",
    "Tokpa",
    "Suah",
    "Kollie",
    "Toe",
    "Freeman",
    "Johnson",
    "Brown",
    "Harris",
)

# New teachers only. Grace Johnson is the thirteenth Gardnersville teacher.
POPULATION = (
    ("gardnersville", 400, 12),
    ("paynesville", 2400, 80),
    ("sinkor", 1800, 60),
)


def display_name(index):
    given = GIVEN_NAMES[index % len(GIVEN_NAMES)]
    surname = SURNAMES[(index // len(GIVEN_NAMES)) % len(SURNAMES)]
    return f"{given} {surname}"


def population_plan():
    """One row per new student and new teacher."""
    rows = []
    index = 0
    for campus, students, teachers in POPULATION:
        for number in range(1, students + 1):
            rows.append(
                {
                    "email": f"student.{campus}.{number:04d}@gojo.test",
                    "display_name": display_name(index),
                    "role": "student",
                    "campus": campus,
                }
            )
            index += 1
        for number in range(1, teachers + 1):
            rows.append(
                {
                    "email": f"teacher.{campus}.{number:02d}@gojo.test",
                    "display_name": display_name(index),
                    "role": "teacher",
                    "campus": campus,
                }
            )
            index += 1
    return rows

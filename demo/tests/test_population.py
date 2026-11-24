"""Email split for the Gojo campus population."""

from django.test import SimpleTestCase

from demo.service.population import POPULATION, population_plan


class PopulationPlanTests(SimpleTestCase):
    def test_counts_and_emails_match_the_campuses(self):
        rows = population_plan()
        emails = [row["email"] for row in rows]
        self.assertEqual(len(emails), len(set(emails)))
        for campus, students, teachers in POPULATION:
            campus_rows = [row for row in rows if row["campus"] == campus]
            self.assertEqual(sum(row["role"] == "student" for row in campus_rows), students)
            self.assertEqual(sum(row["role"] == "teacher" for row in campus_rows), teachers)
        self.assertTrue(emails[0].startswith("student.gardnersville.0001@"))
        self.assertIn("teacher.paynesville.01@gojo.test", emails)
        self.assertTrue(all(row["display_name"] for row in rows))

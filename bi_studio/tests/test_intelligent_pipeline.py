import unittest

import pandas as pd

from bi_studio.utils.data_types import parse_number_series, profile_dataframe
from bi_studio.utils.quality import calculate_quality_score


def by_original(profiles):
    return {profile.original_column_name: profile for profile in profiles}


class TestIntelligentPipeline(unittest.TestCase):
    def test_employee_dataset_semantics(self):
        df = pd.DataFrame(
            {
                "Employee ID": ["E001", "E002", "E003"],
                "Name": ["Amina", "Bola", "Chen"],
                "Gender": ["F", "M", "Female"],
                "Department": ["Sales", "Sales", "Finance"],
                "Start Date": ["2024-01-02", "2024-02-01", "2024-03-15"],
                "Monthly Salary": [1000, 1200, 1300],
                "Annual Salary": [12000, 14400, 15600],
            }
        )
        _, profiles = profile_dataframe(df)
        profiles_by_name = by_original(profiles)

        self.assertEqual(profiles_by_name["Start Date"].semantic_role, "Date Dimension")
        self.assertEqual(profiles_by_name["Monthly Salary"].semantic_role, "Measure")
        self.assertEqual(profiles_by_name["Gender"].semantic_role, "Dimension")
        self.assertEqual(profiles_by_name["Employee ID"].semantic_role, "Identifier")
        self.assertEqual(parse_number_series(df["Monthly Salary"]).sum(), 3500)

    def test_malformed_dataset_profiles_and_quality(self):
        df = pd.DataFrame(
            {
                " Monthly Salary ": ["1,200", None, "bad"],
                "Start Date": ["2024-01-01", "not a date", None],
                "Gender": ["M", "male", "Female"],
            }
        )
        normalized_df, profiles = profile_dataframe(df)
        profiles_by_name = by_original(profiles)
        quality = calculate_quality_score(normalized_df, {"monthly_salary": 1, "start_date": 1}, unusable_columns=0)

        self.assertIn("monthly_salary", normalized_df.columns)
        self.assertEqual(profiles_by_name[" Monthly Salary "].semantic_role, "Measure")
        self.assertEqual(profiles_by_name["Start Date"].semantic_role, "Date Dimension")
        self.assertLess(quality["score"], 100)

    def test_unusable_columns_are_ignored(self):
        df = pd.DataFrame(
            {
                "Customer ID": [1001, 1002, 1003],
                "Empty Column": [None, None, None],
                "Constant": ["same", "same", "same"],
                "Long Notes": ["long text " * 20, "another text " * 20, "more text " * 20],
            }
        )
        _, profiles = profile_dataframe(df)
        profiles_by_name = by_original(profiles)

        self.assertEqual(profiles_by_name["Customer ID"].semantic_role, "Identifier")
        self.assertEqual(profiles_by_name["Empty Column"].semantic_role, "Ignored")
        self.assertEqual(profiles_by_name["Constant"].semantic_role, "Ignored")


if __name__ == "__main__":
    unittest.main()

from job_agent.validate import validate_materials


def test_unverified_number_flagged():
    p = {"experience": [{"company": "Nimbus Motors"}]}
    assert validate_materials({"summary": "Delivered 2500 products."}, p)

from __future__ import annotations

import unittest

from director_orchestrator.director import _strict_schema
from director_orchestrator.models import DirectorPlan, ReviewDecision


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


class StrictSchemaTests(unittest.TestCase):
    def test_every_object_requires_all_properties_and_forbids_extras(self):
        for model, name in ((DirectorPlan, "plan"), (ReviewDecision, "review")):
            schema = _strict_schema(model, name)["schema"]
            for node in _walk(schema):
                self.assertNotIn("default", node)
                if isinstance(node.get("properties"), dict):
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))


if __name__ == "__main__":
    unittest.main()


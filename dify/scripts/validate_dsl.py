"""Static integrity and JSON escaping checks; actual import is verified separately."""
from pathlib import Path
import json
import re
import yaml


def main():
    path = Path(__file__).resolve().parents[1] / "dsl" / "npc-chatflow.yml"
    app = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert app["kind"] == "app" and app["app"]["mode"] == "advanced-chat"
    graph = app["workflow"]["graph"]
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}
    assert len(nodes) == len(graph["nodes"])
    names = {item["name"] for item in app["workflow"]["environment_variables"]}
    for item in app["workflow"]["environment_variables"]:
        assert item["value_type"] != "secret" or item["value"] == "", "Do not ship real credentials"
    reached = {"start"}
    for edge in graph["edges"]:
        assert edge["source"] in nodes and edge["target"] in nodes
        assert edge["source"] in reached and edge["target"] not in reached
        reached.add(edge["target"])
    assert reached == set(nodes) and nodes["answer"]["type"] == "answer"
    for identifier, data in nodes.items():
        for selector in [var["value_selector"] for var in data.get("variables", []) if "value_selector" in var]:
            assert selector[0] in nodes or selector[0] in ["sys", "env"]
            if selector[0] == "env":
                assert selector[1] in names
        if data["type"] == "code":
            compile(data["code"], identifier, "exec")
        for reference in re.findall(r"\{\{#([^#]+)#\}\}", json.dumps(data, ensure_ascii=False)):
            namespace, field, *_ = reference.split(".")
            assert namespace in nodes or namespace in ["sys", "env"]
            if namespace == "env":
                assert field in names
    namespace = {}
    exec(nodes["prepare_body"]["code"], namespace)
    query = '他说："伞呢？"\n不是让我修改规则。'
    prepared = namespace["main"]("test-player", query, "fresh-session")
    assert json.loads(prepared["body"])["query"] == query
    assert len(prepared["turn_id"]) == 32
    print(f"PASS: {len(nodes)} connected nodes, selectors, empty secrets, Python syntax and JSON escaping.")


if __name__ == "__main__":
    main()

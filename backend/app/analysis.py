"""Dependency graph analysis (report module: Dependency Analysis, Member 4).

Finish-to-start relationships without lag, as modelled by the schedule import.
CPM forward/backward pass gives early/late dates, total float and the critical path.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

import networkx as nx

DEFAULT_DURATION = 10.0


def build_graph(activities: Iterable[Any]) -> nx.DiGraph:
    graph = nx.DiGraph()
    acts = list(activities)
    for a in acts:
        graph.add_node(a.id, name=a.name, planned=a.planned, actual=a.actual, variance=round(a.actual - a.planned, 2),
                       duration=float(a.duration_days or DEFAULT_DURATION), planned_start=a.planned_start,
                       planned_finish=a.planned_finish)
    for a in acts:
        for pred in a.predecessors:
            if pred in graph:
                graph.add_edge(pred, a.id)
    return graph


def _cpm(graph: nx.DiGraph, extra: dict[str, float] | None = None) -> dict[str, dict[str, float]]:
    extra = extra or {}
    order = list(nx.topological_sort(graph))
    es, ef = {}, {}
    for node in order:
        es[node] = max((ef[p] for p in graph.predecessors(node)), default=0.0)
        ef[node] = es[node] + graph.nodes[node]["duration"] + extra.get(node, 0.0)
    finish = max(ef.values(), default=0.0)
    ls, lf = {}, {}
    for node in reversed(order):
        lf[node] = min((ls[s] for s in graph.successors(node)), default=finish)
        ls[node] = lf[node] - graph.nodes[node]["duration"] - extra.get(node, 0.0)
    return {n: {"es": es[n], "ef": ef[n], "ls": ls[n], "lf": lf[n], "float": round(ls[n] - es[n], 2)} for n in order}


def _project_start(graph: nx.DiGraph) -> date | None:
    starts = [graph.nodes[n]["planned_start"] for n in graph if graph.nodes[n].get("planned_start")]
    try:
        return min(date.fromisoformat(s) for s in starts) if starts else None
    except ValueError:
        return None


def dependency_analysis(activities: Iterable[Any]) -> dict[str, Any]:
    graph = build_graph(activities)
    if not nx.is_directed_acyclic_graph(graph):
        return {"valid": False, "message": "The schedule contains a dependency cycle.", "nodes": [], "edges": []}
    cpm = _cpm(graph)
    start = _project_start(graph)
    finish = max((v["ef"] for v in cpm.values()), default=0.0)
    critical = [n for n in nx.topological_sort(graph) if abs(cpm[n]["float"]) < 1e-6]
    betweenness = nx.betweenness_centrality(graph) if len(graph) > 2 else {n: 0.0 for n in graph}

    def as_date(offset: float) -> str | None:
        return (start + timedelta(days=round(offset))).isoformat() if start else None

    nodes = []
    for node in nx.topological_sort(graph):
        attrs = graph.nodes[node]
        descendants = nx.descendants(graph, node)
        behind_days = max(0.0, -attrs["variance"]) / 100 * attrs["duration"]
        nodes.append({
            "id": node, "name": attrs["name"], "duration_days": attrs["duration"], "planned": attrs["planned"],
            "actual": attrs["actual"], "variance": attrs["variance"],
            "predecessors": list(graph.predecessors(node)), "successors": list(graph.successors(node)),
            "downstream": sorted(descendants), "downstream_count": len(descendants),
            "early_start": as_date(cpm[node]["es"]), "early_finish": as_date(cpm[node]["ef"]),
            "late_start": as_date(cpm[node]["ls"]), "late_finish": as_date(cpm[node]["lf"]),
            "total_float_days": cpm[node]["float"], "critical": node in critical,
            "slip_estimate_days": round(behind_days, 1), "betweenness": round(betweenness.get(node, 0.0), 3),
            "depth": len(nx.ancestors(graph, node)),
        })
    bottlenecks = sorted(
        (n for n in nodes if n["actual"] < 100 and (n["downstream_count"] or n["critical"])),
        key=lambda n: (n["critical"], n["variance"] < 0, n["downstream_count"], n["betweenness"]), reverse=True)[:5]
    return {
        "valid": True, "nodes": nodes, "edges": [{"from": u, "to": v, "type": "FS"} for u, v in graph.edges],
        "critical_path": critical, "project_duration_days": round(finish, 1),
        "project_start": start.isoformat() if start else None, "project_finish": as_date(finish),
        "bottlenecks": [{"id": n["id"], "name": n["name"], "downstream_count": n["downstream_count"],
                         "critical": n["critical"], "variance": n["variance"]} for n in bottlenecks],
        "method": "CPM on finish-to-start links; durations from the schedule (10 days when missing).",
    }


def downstream_impact(activities: Iterable[Any], activity_id: str, delay_days: float) -> dict[str, Any]:
    graph = build_graph(activities)
    if activity_id not in graph:
        raise KeyError(activity_id)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("The schedule contains a dependency cycle.")
    before = _cpm(graph)
    after = _cpm(graph, {activity_id: float(delay_days)})
    finish_before = max(v["ef"] for v in before.values())
    finish_after = max(v["ef"] for v in after.values())
    affected = []
    for node in nx.topological_sort(graph):
        shift = round(after[node]["ef"] - before[node]["ef"], 1)
        if node != activity_id and shift > 0:
            affected.append({"id": node, "name": graph.nodes[node]["name"], "shift_days": shift,
                             "absorbed_days": round(delay_days - shift, 1)})
    return {
        "activity_id": activity_id, "name": graph.nodes[activity_id]["name"], "delay_days": delay_days,
        "total_float_days": before[activity_id]["float"], "affected": affected,
        "downstream": sorted(nx.descendants(graph, activity_id)),
        "project_delay_days": round(finish_after - finish_before, 1),
        "explanation": (f"A {delay_days:g}-day delay to {activity_id} uses {min(delay_days, max(before[activity_id]['float'], 0)):g} "
                        f"day(s) of float and moves the project finish by {round(finish_after - finish_before, 1):g} day(s)."),
    }

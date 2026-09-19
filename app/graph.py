from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.llm import LLM
from app.nodes import (
    Tools,
    make_analyst,
    make_critic,
    make_planner,
    make_scout,
    make_strategist,
    make_thesis,
    make_writer,
)
from app.state import ResearchState


def build_graph(llm: LLM, tools: Tools, checkpointer):
    propose_topic, approve_topic = make_strategist(llm)
    propose_thesis, approve_thesis = make_thesis(llm)

    g = StateGraph(ResearchState)
    g.add_node("analyst", make_analyst(llm))
    g.add_node("propose_topic", propose_topic)
    g.add_node("approve_topic", approve_topic)
    g.add_node("scout", make_scout(llm, tools))
    g.add_node("propose_thesis", propose_thesis)
    g.add_node("approve_thesis", approve_thesis)
    g.add_node("planner", make_planner(llm))
    g.add_node("writer", make_writer(llm))
    g.add_node("critic", make_critic(llm))

    g.add_edge(START, "analyst")
    g.add_edge("analyst", "propose_topic")
    g.add_edge("propose_topic", "approve_topic")
    g.add_conditional_edges(
        "approve_topic", lambda s: "propose_topic" if s.get("topic_feedback") else "scout",
        ["propose_topic", "scout"],
    )
    # First pass: scout -> thesis. On a revision round (outline already exists), new evidence must be
    # folded into a fresh plan, or the writer would never see the newly found sources.
    g.add_conditional_edges(
        "scout", lambda s: "planner" if s.get("outline") else "propose_thesis",
        ["planner", "propose_thesis"],
    )
    g.add_edge("propose_thesis", "approve_thesis")
    g.add_conditional_edges(
        "approve_thesis", lambda s: "propose_thesis" if s.get("thesis_feedback") else "planner",
        ["propose_thesis", "planner"],
    )
    g.add_edge("planner", "writer")
    g.add_edge("writer", "critic")

    def after_critic(s):
        crit = s["critique"]
        if crit["approved"] or s.get("revision_count", 0) >= settings.max_revisions:
            return END  # hard cap: a picky critic cannot loop forever
        return "scout" if crit.get("needs_more_sources") else "writer"

    g.add_conditional_edges("critic", after_critic, ["scout", "writer", END])
    return g.compile(checkpointer=checkpointer)


import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from assignment_tools import list_current_assignments, list_my_courses

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_llm() -> ChatGoogleGenerativeAI:
    """Create the Gemini model using the configured API key."""
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Check the project root .env file."
        )

    return ChatGoogleGenerativeAI(
        model="gemini-3.8-flash",
        google_api_key=api_key,
        temperature=0.2,
    )


def build_agent(student_id: str, semester: int):
    """Build a graph scoped to the authenticated student's ERP data."""

    @tool
    def get_my_courses() -> list[dict[str, Any]]:
        """Get my enrolled courses and teachers for the current semester."""
        return list_my_courses(student_id, semester)

    @tool
    def get_my_assignments() -> list[dict[str, Any]]:
        """Get my current-semester assignments, due dates, and submission statuses."""
        return list_current_assignments(student_id, semester)

    tools = [get_my_courses, get_my_assignments]
    llm = get_llm().bind_tools(tools)

    def assistant_node(state: MessagesState):
        system_message = SystemMessage(
            content=(
                "You are an ERP student assistant. Use ERP tools for questions "
                "about courses, assignments, deadlines, and submission status. "
                "Base answers on returned data; never invent assignments, dates, "
                "teachers, or statuses. The assignment tool returns all assignments "
                "available to this student in the current semester. Status values "
                "include pending, submitted, late, and possibly overdue. If there "
                "are no matching records, say so clearly. Never ask for or change "
                "the student's identity. Present lists clearly with headings and "
                "one item per line. Interpret 'next' or 'nearest' deadline as the "
                "earliest upcoming deadline, not the latest one."
            )
        )

        response = llm.invoke([system_message, *state["messages"]])
        return {"messages": [response]}

    workflow = StateGraph(MessagesState)
    workflow.add_node("assistant", assistant_node)
    workflow.add_node("tools", ToolNode(tools))

    workflow.add_edge(START, "assistant")
    workflow.add_conditional_edges("assistant", tools_condition)
    workflow.add_edge("tools", "assistant")

    return workflow.compile()


def invoke_erp_agent(
    question: str,
    student_id: str,
    semester: int,
) -> str:
    """Answer a question using the authenticated student's ERP context."""
    agent = build_agent(student_id, semester)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]}
    )

    final_message = result["messages"][-1]
    return final_message.text

from app.copilot.answers import sanitize_user_answer, user_facing_answer
from app.copilot.schemas import ChatResponse


def test_sanitize_keeps_a_plain_conclusion() -> None:
    assert sanitize_user_answer("WELL-0001 is not producing oil.") == "WELL-0001 is not producing oil."


def test_sanitize_drops_tool_inventory_and_self_talk() -> None:
    text = (
        "Let me re-examine the tool definitions.\n"
        'Tools: [{"name":"get_well_status"},{"name":"get_downtime"},{"name":"get_effective_spills"}]\n'
        "I will now call get_well_status."
    )
    cleaned = sanitize_user_answer(text)
    assert "re-examine" not in cleaned.lower()
    assert "get_well_status" not in cleaned
    assert "tool definitions" not in cleaned.lower()


def test_user_facing_answer_prefers_json_over_preamble() -> None:
    answer, visual = user_facing_answer(
        "Let me look at the tools.\n"
        '{"answer":"No oil-volume field is stored.","visual":{"type":"status_card"}}'
    )
    assert answer == "No oil-volume field is stored."
    assert visual == {"type": "status_card"}


def test_chat_response_validator_strips_deliberation() -> None:
    response = ChatResponse(
        answer="Let me re-examine the tool definitions.\nWELL-0001 is not producing oil."
    )
    assert response.answer == "WELL-0001 is not producing oil."
    assert "re-examine" not in response.model_dump_json()

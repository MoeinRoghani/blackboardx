"""One contract, decoded tolerantly, so the two halves can ship apart."""

import pytest

from blackboard import Level, Premise
from blackboard.wire import (
    AckRequest,
    BoardChangeBody,
    BoardPage,
    ConflictBody,
    ContributionBody,
    LevelPage,
    NotificationBody,
    PremiseBody,
    RegionBody,
    RegionList,
    RejectedBody,
    SetPremiseRequest,
    WireError,
    WriteRequest,
    WrittenBody,
)

BODIES = [
    NotificationBody(board_id="b", notification_id=1, agent="ocp"),
    WriteRequest(writer="ocp", level="platform", content={"findings": ["oom"]}),
    SetPremiseRequest(writer="ocp", premise="window", expected_version=1, value="w"),
    AckRequest(agent="ocp", notification_id=1),
    WrittenBody(sequence=4),
    WrittenBody(sequence=4, version=2),
    ConflictBody(current_version=3),
    RejectedBody(cause="admission", reason="a duplicate"),
    ContributionBody(sequence=1, content="a finding"),
    BoardChangeBody(sequence=1, region="platform", content="a finding"),
    PremiseBody(version=2, value=["a", "b"]),
    RegionBody(name="platform", kind="level"),
]


@pytest.mark.parametrize("body", BODIES, ids=lambda b: type(b).__name__)
def test_a_body_survives_a_round_trip(body: object) -> None:
    assert type(body).from_json(body.to_json()) == body  # type: ignore[attr-defined]


@pytest.mark.parametrize("page", [LevelPage(), BoardPage(), RegionList()])
def test_an_empty_page_survives_a_round_trip(page: object) -> None:
    assert type(page).from_json(page.to_json()) == page  # type: ignore[attr-defined]


def test_a_page_survives_a_round_trip() -> None:
    page = LevelPage(
        contributions=[ContributionBody(sequence=1, content="a")], has_more=True
    )
    assert LevelPage.from_json(page.to_json()) == page


class TestDecodingTolerantly:
    def test_a_field_the_decoder_does_not_know_is_ignored(self) -> None:
        body = {"sequence": 4, "version": 2, "something_added_later": "ignored"}
        assert WrittenBody.from_json(body) == WrittenBody(sequence=4, version=2)

    def test_a_field_that_is_absent_takes_its_default(self) -> None:
        assert WrittenBody.from_json({"sequence": 4}) == WrittenBody(sequence=4)

    def test_an_older_half_reads_a_newer_notification(self) -> None:
        newer = {
            "board_id": "b",
            "notification_id": 1,
            "agent": "ocp",
            "from_sequence": 2,
            "to_sequence": 5,
            "regions": ["window"],
            "priority": "high",
        }
        decoded = NotificationBody.from_json(newer)
        assert decoded.regions == ["window"]
        assert decoded.to_sequence == 5

    def test_a_field_the_body_cannot_do_without_is_refused(self) -> None:
        with pytest.raises(WireError, match="notification_id"):
            NotificationBody.from_json({"board_id": "b", "agent": "ocp"})

    def test_something_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(WireError, match="object"):
            WrittenBody.from_json([1, 2, 3])


class TestRegions:
    def test_a_declaration_survives_the_wire(self) -> None:
        for region in (Level("platform"), Premise("window")):
            assert RegionBody.of(region).declaration() == region

    def test_a_list_of_regions_survives_the_wire(self) -> None:
        listed = RegionList(
            regions=[RegionBody.of(Level("platform")), RegionBody.of(Premise("window"))]
        )
        assert RegionList.from_json(listed.to_json()) == listed

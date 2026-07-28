"""Panel aggregation. Majority of the judges that actually voted.

The rules, and why each one is what it is:

  - Abstentions do not vote. Counting an abstention as a fail would let a broken
    judge quietly veto every case; counting it as a pass would let it wave
    everything through. Either way the panel would be reporting a verdict it did
    not reach.
  - Fewer than 2 voting judges means the panel abstains. One opinion is not a
    panel, and calling it one is how a degraded run gets reported as a healthy
    one.
  - A tie means the panel abstains AND the case is flagged for human escalation.
    That is a feature. A gate that resolves genuine judge disagreement by coin
    flip, or by whichever judge happens to be listed first, is manufacturing
    confidence it does not have. The ambiguous cases in the golden set exist to
    make sure this path is exercised rather than theoretical.

THE SHADOW JUDGE INVARIANT

A shadow judge can NEVER change a gate outcome. If it could, it would be a voting
judge with extra steps, and the whole reason to run a cheap judge in shadow is to
learn what it would have said without betting a release on the answer.

That is enforced here rather than documented and hoped for: `aggregate` raises on
any verdict carrying `shadow=True`. Voting and shadow verdicts are built by
separate factories, travel on separate fields of the run result, and land in
separate lists in the calibration report, so a shadow verdict reaching the vote
would have to get past a type level mistake and then this check.
"""

from __future__ import annotations

from eval_gate.models import JudgeVerdict, PanelVerdict


def aggregate(
    verdicts: list[JudgeVerdict],
    *,
    degraded: bool = False,
) -> PanelVerdict:
    """Combine one repeat's per judge verdicts into the panel's verdict."""
    if not verdicts:
        raise ValueError("cannot aggregate an empty panel")
    shadows = sorted(item.judge_name for item in verdicts if item.shadow)
    if shadows:
        # Structural, not advisory. A shadow judge in the vote is the one bug
        # this design cannot tolerate, so it fails loudly at the boundary rather
        # than quietly shifting a majority.
        raise ValueError(
            f"shadow judges cannot vote: {', '.join(shadows)}"
        )

    case_id = verdicts[0].case_id
    sut_version = verdicts[0].sut_version
    repeat = verdicts[0].repeat
    votes = tuple(sorted(item.verdict for item in verdicts))
    abstentions = sum(1 for item in verdicts if item.verdict == "abstain")
    voting = [item.verdict for item in verdicts if item.verdict != "abstain"]

    passes = voting.count("pass")
    fails = voting.count("fail")

    if len(verdicts) == 1:
        # A one judge configuration is not a panel, and the fewer-than-two-voters
        # rule below would make it abstain on every case, which would make single
        # judge mode useless for the cost comparison it exists to enable. So a
        # single judge reports its own verdict, and the run is labeled as
        # single judge rather than as a panel.
        verdict = verdicts[0].verdict
        escalated = verdict == "abstain"
    elif len(voting) < 2:
        verdict = "abstain"
        escalated = True
    elif passes > fails:
        verdict = "pass"
        escalated = False
    elif fails > passes:
        verdict = "fail"
        escalated = False
    else:
        verdict = "abstain"
        escalated = True

    unanimous = len(set(votes)) == 1
    split = len({item for item in voting}) > 1

    return PanelVerdict(
        case_id=case_id,
        sut_version=sut_version,
        verdict=verdict,
        votes=votes,
        unanimous=unanimous,
        split=split,
        abstentions=abstentions,
        degraded=degraded,
        escalated=escalated,
        repeat=repeat,
    )

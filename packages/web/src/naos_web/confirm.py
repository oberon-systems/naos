from dataclasses import dataclass

from naos_web.rows import Fact, RunDetailRow, RunnerDetailRow

LEAD = "A new run will be created with these options:"


@dataclass(frozen=True)
class Confirm:
    title: str
    subject: str
    question: str
    action: str
    back: str
    footer: str
    danger: bool = False
    note: str | None = None
    lead: str | None = None
    facts: tuple[Fact, ...] = ()
    key: str | None = None


def stop(run: RunDetailRow) -> Confirm:
    return Confirm(
        title="Stop run?",
        subject=run.id,
        question=f"Are you sure you want to stop {run.id}?",
        note="The runner shuts the VM down and the run ends as STOPPED. Nothing is retried.",
        action=f"/runs/{run.id}/stop",
        back=f"/runs/{run.id}",
        footer="the audit keeps who stopped it",
        danger=True,
    )


# The rerun carries the spec and nothing else, so the new run waits for whichever
# runner is free rather than the one that held this one.
def rerun(run: RunDetailRow, key: str) -> Confirm:
    facts = {fact.label: fact for fact in run.run} | {fact.label: fact for fact in run.policy}
    options = [facts[label] for label in ("Spec", "Image", "Profile") if label in facts]
    options.append(Fact("Runner", "any live runner with a free slot"))
    if "Timeouts" in facts:
        options.append(facts["Timeouts"])
    return Confirm(
        title="Rerun?",
        subject=run.id,
        question=f"Are you sure you want to rerun {run.id}?",
        lead=LEAD,
        facts=tuple(options),
        action=f"/runs/{run.id}/rerun",
        back=f"/runs/{run.id}",
        footer="a new run id is issued \u00b7 this run is kept",
        key=key,
    )


def revoke(runner: RunnerDetailRow) -> Confirm:
    return Confirm(
        title="Revoke runner?",
        subject=runner.id,
        question=f"Are you sure you want to revoke {runner.name}?",
        note=(
            "Its tokens are refused from now on and its lease ends at once: the runs it holds "
            "fail as runner revoked and its pending runs go back to the queue."
        ),
        action=f"/runners/{runner.id}/revoke",
        back=f"/runners/{runner.id}",
        footer="the audit keeps who revoked it",
        danger=True,
    )


def drain(runner: RunnerDetailRow) -> Confirm:
    return Confirm(
        title="Drain runner?",
        subject=runner.id,
        question=f"Are you sure you want to drain {runner.name}?",
        note="It takes no new run; the runs it holds finish as usual.",
        action=f"/runners/{runner.id}/drain",
        back=f"/runners/{runner.id}",
        footer="the audit keeps who drained it",
    )

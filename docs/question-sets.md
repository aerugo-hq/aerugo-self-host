# Question sets per team

Every change to how Aerugo answers is held to the questions each team actually asks (F7). A team keeps its questions in
the company's onboarding repository, beside the files that already describe it:

```
teams/
  stores/
    team.yaml        # who's on the team (G4)
    sources.yaml     # what it connects
    questions.yaml   # what its people ask, and what a right answer is
```

`CODEOWNERS` gives each team's folder to that team, so a team reviews changes to its own questions.

## The file

```yaml
version: 1
team: stores                 # the team's slug
model: stores/assistant      # optional: the model its people ask through, from the team's own registry
cases:
  - id: registers-down       # lowercase letters, digits, and dashes; once per team
    asker: test-store-assoc  # a test account with the role and place this question is asked from
    question: The registers are down. What do we do?
    kind: procedure          # a short word to group cases by: lookup, procedure, location, permission...
    expect: [confluence-71008]   # the pages a right answer draws on, by their key in Aerugo
    units: [Registers offline]   # optional: the knowledge units it should lead with, by title
    forbid: [confluence-71034]   # pages this asker must never see
    needs: F8                # optional: a slice the case waits on (run and shown, not counted)
```

Askers are test accounts in the evaluation environment, never real people: a question set is a test, and it's
committed to a repository.

## Running it

`make eval-teams` runs every `*/questions.yaml` under a folder (the synthetic retailer's,
`sandbox/scenarios/retail/teams`, by default; `python -m kl.team_evals --folder <path>` for another). Each question is
asked as its asker through search, and through Ask when the team names a model. Each team is scored on its own:

- how many of its right pages are found in the top ten, and where the first one lands;
- whether Ask cites a right page, and whether it leads with a right knowledge unit.

A page an asker must never see, in search results or in an answer's citations, is a permission leak and fails the run.
Each team's scorecard is held to its own baseline (`teams` in `services/api/kl/evals_baseline.json`): a case that finds
less, whose first right page falls, or that stops leading with a right unit fails the run, naming the team and the
case. `make eval-baseline` records the baselines deliberately, after the other sets, as CI orders them.

## Growing it

`python -m kl.team_evals --mine <team>` prints candidate cases from what that team's people asked in the last month
(F5a keeps questions for `KL_QUESTION_DAYS`): only wordings that at least `KL_GAP_MIN_ASKERS` different people used and
were answered well. Each candidate leaves its asker as `TODO-test-account` and its right pages empty for a person to
fill in; no real person's name ever lands in a file. What people asked and weren't answered well is listed as a gap:
content to write, not a test case.

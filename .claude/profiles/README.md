# Module Profiles — example templates

> **These files are starter templates, not a fixed module set.** The
> profile names (`backend`, `worker`, `frontend`, `shared`) are *example*
> labels for common architectural roles. Rename them, drop the ones you
> don't need, add ones we don't have — to match your project's actual
> module layout.

## What a profile is

A profile is a YAML file that tells the pipeline:
- which files belong to this module (`paths`)
- how to build, test, lint this module (`build`, `test`, `lint`)
- what the module's risk level is (`default_risk_level`)
- which manifesto-axis checks the reviewer should enforce here
  (`manifest_check`)
- which downstream modules cascade-test on changes (`cascade.triggers`)
- which modules this depends on (`depends_on`)

The agents (planner, analyst, architect, reviewer, tester, qa) all read
the profile for the module they're working on. Drop in commands that work
on your stack — Go, Rust, Java, Python, Node, anything — the agents call
whatever you put there.

## What's shipped here (and how to customize)

| File | Plays the role of | Adapt by |
|---|---|---|
| `backend.yaml` | API server / REST or GraphQL backend | Rename to your backend module name; replace commands |
| `worker.yaml` | Background-job / queue-consumer / async-processor module | Rename if your worker module is named differently |
| `frontend.yaml` | UI module | Rename if your UI directory is named differently |
| `shared.yaml` | Cross-cutting library shared by other modules | Rename to your common-library module |

## Adapting to your project

1. **Inventory your modules.** What are the natural seams in your codebase?
   `apps/foo/` and `apps/bar/` and `packages/baz/`?
2. **One profile per module.** Create a `.claude/profiles/<your-module>.yaml`
   for each. Use the existing files as templates.
3. **Fill in the commands.** Replace every `<your build command for the X
   module>` placeholder with what your stack actually runs.
4. **Set `paths`.** Glob-pattern that matches your module's actual directory layout. Verify with `find . -path './<your-pattern>'` before running the pipeline. Example: if your backend lives at `libs/server/`, set `paths: ["libs/server/**"]` — don't leave the example `apps/backend/**` in place.
5. **Tune `manifest_check`.** Add the things that *actually* fail in your
   reviews. We've found the lists you ship with grow over time as the
   retrospective agent surfaces patterns.

## Risk levels (how they're used)

| `default_risk_level` | Effect |
|---|---|
| `LOW` | Standard pipeline, normal retry caps |
| `MEDIUM` | Standard pipeline; reviewer does a slightly deeper read |
| `HIGH` | Module guard — daemon serializes runs on this module to prevent conflicting branches |
| `CRITICAL` | Module guard + cascade tests + tightened security review |

## Cascade

If you change the shared library, every module that depends on it should
re-test. Express that with `cascade.triggers: [downstream_module_a, ...]`.
The tester agent runs each listed downstream module's `test` command after
the main one.

## Don't forget

- The `manifest_check` items propagate into the reviewer agent — empty
  manifest_check = unenforced manifesto. Even four bullet items is better
  than zero.
- `paths` patterns are matched by the `after-code-change.sh` hook to
  detect which module a changed file belongs to. Keep them tight; broad
  globs cause the wrong module to be detected.
- These profiles are *templates*, not *gospel*. Delete the ones you don't
  use. Your repo only needs profiles for modules you actually have.

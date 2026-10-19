# Repository submodules

Keep Git submodules in this directory. They are development resources and are
not required by the trader, dashboard, Python packages, or Docker runtime.

| Directory | Upstream | Purpose |
| --- | --- | --- |
| `skills/` | [wecet/skills](https://github.com/wecet/skills) | Shared agent skills and coding guidance |

Initialize the pinned revisions after cloning:

```sh
git submodule update --init --recursive
```

To clone with submodules in one step:

```sh
git clone --recurse-submodules https://github.com/wecet/ai-based-autotrader.git
```

Update submodule revisions intentionally and commit the updated pointer in the
parent repository. Keep each submodule's own changes and commits in its upstream
repository.

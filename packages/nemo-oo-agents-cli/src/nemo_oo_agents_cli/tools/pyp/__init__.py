"""Async-native composable shell piping in Python with method-chaining streams."""

from nemo_oo_agents.skill import Skill
from nemo_oo_agents_cli.tools.pyp.errors import PipeError as PipeError
from nemo_oo_agents_cli.tools.pyp.errors import Result as Result
from nemo_oo_agents_cli.tools.pyp.errors import make_pipe_error as make_pipe_error
from nemo_oo_agents_cli.tools.pyp.sources import arun as arun
from nemo_oo_agents_cli.tools.pyp.sources import cat as cat
from nemo_oo_agents_cli.tools.pyp.sources import empty as empty
from nemo_oo_agents_cli.tools.pyp.sources import find as find
from nemo_oo_agents_cli.tools.pyp.sources import glob as glob
from nemo_oo_agents_cli.tools.pyp.sources import items as items
from nemo_oo_agents_cli.tools.pyp.sources import lines as lines
from nemo_oo_agents_cli.tools.pyp.sources import rg as rg
from nemo_oo_agents_cli.tools.pyp.sources import run as run
from nemo_oo_agents_cli.tools.pyp.sources import seq as seq
from nemo_oo_agents_cli.tools.pyp.sources import stdin as stdin
from nemo_oo_agents_cli.tools.pyp.stream import Stream as Stream


class Pyp(Skill):
    """Async-native shell piping in Python: method-chaining streams + rg + structured errors.

    Usage:
        errors = await self.pyp.cat("app.log").grep("ERROR").head(10).collect()
        files  = await self.pyp.find("src", name="*.py").sort().collect()
        todos  = await self.pyp.rg("TODO", type_filter="py").wc().first()
        table  = await self.pyp.run("ps aux").head(5).table()
        text   = await self.pyp.cat("f.txt").grep("key").text()

    Sources:      .cat()  .run()  .arun()  .find()  .glob()  .rg()  .seq()
                  .lines()  .items()  .empty()  .stdin()
    Transforms:   .grep()  .head()  .tail()  .sort()  .uniq()  .cut()  .sed()  .wc()
                  .skip()  .tee()  .flatten()  .strip()  .map()  .filter()
                  .xargs(fn)  .take_while()  .drop_while()  .pipe(*fns)
    Sinks:        .collect()  .first()  .last()  .count()  .text()  .table()
                  .result()  .write(path)  .json()  .to_set()  .to_dict()

    Errors:       PipeError (step, cmd, returncode, stderr, pipeline_repr)
                  Result    (.ok, .lines, .returncode, .stderr, .text)
    """

    # Expose all source functions as instance methods
    cat = staticmethod(cat)
    run = staticmethod(run)
    arun = staticmethod(arun)
    find = staticmethod(find)
    glob = staticmethod(glob)
    rg = staticmethod(rg)
    seq = staticmethod(seq)
    lines = staticmethod(lines)
    items = staticmethod(items)
    empty = staticmethod(empty)
    stdin = staticmethod(stdin)

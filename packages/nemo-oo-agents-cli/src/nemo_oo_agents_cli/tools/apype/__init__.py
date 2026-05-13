"""Async-native composable shell piping in Python with method-chaining streams."""

from nemo_oo_agents.skill import Skill
from nemo_oo_agents_cli.tools.apype.errors import PipeError as PipeError
from nemo_oo_agents_cli.tools.apype.errors import Result as Result
from nemo_oo_agents_cli.tools.apype.errors import make_pipe_error as make_pipe_error
from nemo_oo_agents_cli.tools.apype.sources import arun as arun
from nemo_oo_agents_cli.tools.apype.sources import cat as cat
from nemo_oo_agents_cli.tools.apype.sources import empty as empty
from nemo_oo_agents_cli.tools.apype.sources import find as find
from nemo_oo_agents_cli.tools.apype.sources import glob as glob
from nemo_oo_agents_cli.tools.apype.sources import items as items
from nemo_oo_agents_cli.tools.apype.sources import lines as lines
from nemo_oo_agents_cli.tools.apype.sources import rg as rg
from nemo_oo_agents_cli.tools.apype.sources import run as run
from nemo_oo_agents_cli.tools.apype.sources import seq as seq
from nemo_oo_agents_cli.tools.apype.sources import stdin as stdin
from nemo_oo_agents_cli.tools.apype.stream import Stream as Stream


class Apype(Skill):
    """Async-native shell piping in Python: method-chaining streams + rg + structured errors.

    Usage:
        errors = await cat("app.log").grep("ERROR").head(10).collect()
        files  = await find("src", name="*.py").sort().collect()
        todos  = await rg("TODO", type_filter="py").wc().first()
        table  = await run("ps aux").head(5).table()
        text   = await cat("f.txt").grep("key").text()

    Sources:      cat  run  arun  find  glob  rg  seq  lines  items  empty  stdin
    Transforms:   .grep()  .head()  .tail()  .sort()  .uniq()  .cut()  .sed()  .wc()
                  .skip()  .tee()  .flatten()  .strip()  .map()  .filter()
                  .take_while()  .drop_while()  .pipe(*fns)
    Sinks:        .collect()  .first()  .last()  .count()  .text()  .table()
                  .result()  .write(path)  .json()  .to_set()  .to_dict()

    Errors:       PipeError (step, cmd, returncode, stderr, pipeline_repr)
                  Result    (.ok, .lines, .returncode, .stderr, .text)
    """

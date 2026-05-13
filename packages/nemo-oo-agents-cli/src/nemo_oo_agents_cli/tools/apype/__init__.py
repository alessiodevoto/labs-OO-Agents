"""Async-native composable shell piping in Python with method-chaining streams."""

from nemo_oo_agents.skill import Skill

from nemo_oo_agents_cli.tools.apype.errors import PipeError, Result, make_pipe_error
from nemo_oo_agents_cli.tools.apype.stream import Stream
from nemo_oo_agents_cli.tools.apype.sources import cat, run, arun, find, glob, stdin, lines, items, empty, rg, seq


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

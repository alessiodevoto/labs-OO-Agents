"""MethodWriting — opt-in capability for agents to define helper methods."""

from agent006.skill import Skill


class MethodWriting(Skill):
    """Define persistent helper methods and LLM-powered sub-methods on the agent. Use this when the agent needs to break complex tasks into reusable helpers.

    Add this skill to an agent when it needs to break complex tasks into
    reusable helpers. Methods defined in execute_python() with `self` as the
    first parameter are bound to the agent and persist across cells.

    ## Plain helper functions
    For deterministic logic (math, filtering, formatting, data transformation).
    No LLM call needed — define inline in execute_python():

        def celsius_to_fahrenheit(c):
            return c * 9/5 + 32

        converted = [celsius_to_fahrenheit(t) for t in temperatures]
        return_result(converted)

    ## @strategy(PredictStrategy()) — LLM sub-methods
    For sub-tasks requiring language understanding: classification, extraction,
    translation, interpretation. The framework calls an LLM to implement them.

        @strategy(PredictStrategy())
        async def classify(self, text: str) -> str:
            \"\"\"Classify this text as positive, negative, or neutral.\"\"\"
            ...

        results = [await self.classify(t) for t in texts]
        return_result(results)

    ## @strategy(CodeActStrategy()) — multi-step LLM sub-methods
    For sub-tasks needing both reasoning and multi-step computation.
    Each runs its own code execution loop.

        @strategy(CodeActStrategy())
        async def analyse(self, data: list) -> AnalysisResult:
            \"\"\"Analyse this data and return structured findings.\"\"\"
            ...

    ## Rules
    - Methods with `self` as first parameter are bound to the agent and persist
    - Methods without `self` are local to the current cell only
    - Use `...` (ellipsis) as the body — the framework implements it via LLM
    - The method's docstring IS the prompt: write it clearly

    ## No heuristics for language understanding
    Never use keyword matching, regex, or hand-written rules for tasks requiring
    language understanding (classification, extraction, interpretation).
    These tasks need LLM reasoning — delegate to a PredictStrategy sub-method.

    Examples:
        @strategy(PredictStrategy())
        async def extract_date(self, text: str) -> str:
            \"\"\"Extract the date from this text in YYYY-MM-DD format.\"\"\"
            ...

        dates = [await self.extract_date(t) for t in texts]

    Load this skill:
        doc(self.writing)
    """

    pass

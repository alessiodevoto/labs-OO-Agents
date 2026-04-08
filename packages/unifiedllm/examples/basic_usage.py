"""
Basic usage example for UnifiedLLM package.

This example demonstrates:
1. Using CompletionClient for structured outputs
2. Function calling with tools
3. HTTP request logging
"""

from pydantic import BaseModel

from unifiedllm import CompletionClient, create_tool_from_callable
from unifiedllm.http_logging import enable_http_request_logging


class WeatherResponse(BaseModel):
    location: str
    temperature: float
    conditions: str


def get_current_weather(location: str, unit: str = "celsius") -> str:
    """Get the current weather for a location"""
    return f"The weather in {location} is 22 degrees {unit} and sunny"


def main():
    disable_logging = enable_http_request_logging(
        output_dir="debug_logs", url_filter="api.openai.com", save_responses=False, verbose=True
    )

    llm = CompletionClient(model="gpt-4o-mini", api_key="your-api-key-here")

    print("Example 1: Structured Output")
    print("-" * 50)

    response = llm.call_llm_with_retry(
        messages=[{"role": "user", "content": "What's the weather like in Paris?"}],
        tools=[],
        output_model=WeatherResponse,
        max_retries=3,
    )
    if not isinstance(response.content, WeatherResponse):
        raise ValueError("Response content is not a WeatherResponse")

    print(f"Location: {response.content.location}")
    print(f"Temperature: {response.content.temperature}°C")
    print(f"Conditions: {response.content.conditions}")

    print("\nExample 2: Function Calling")
    print("-" * 50)

    weather_tool = create_tool_from_callable(get_current_weather)

    response = llm.call(
        messages=[{"role": "user", "content": "What's the weather in London?"}], tools=[weather_tool], output_model=None
    )

    if response.tool_calls:
        for tc in response.tool_calls:
            print(f"Tool called: {tc.name}")
            print(f"Arguments: {tc.arguments}")

    disable_logging()


if __name__ == "__main__":
    main()

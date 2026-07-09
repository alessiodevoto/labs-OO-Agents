# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Vending-Bench agent for nemo-oo-agents-benchmarks.

Vending-Bench (Backlund et al., 2025; arxiv:2502.15840) tests an LLM agent's
ability to manage a vending machine business over hundreds of simulated days.

The agent interacts with the simulation via Python tool calls:
  - search_suppliers / order_products / stock_machine / set_price
  - collect_earnings / wait_for_next_day
  - write_note / read_note / list_notes (persistent memory)

Primary metric: final net worth = cash + uncollected machine earnings
  + wholesale value of unsold inventory.

Paper results (original benchmark):
  Claude 3.5 Sonnet: $2,217 mean net worth (best)
  Human baseline:    $844 net worth

Architecture:
  - Single CodeAct agent; simulation tools loaded from /app/simulation.py
  - Memory tools exposed as agent methods for the LLM to plan with
  - Context block shows current balance + inventory state each turn
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from unifiedllm import FakeLLMClient

from nooa import Agent, CodeActStrategy, strategy
from nooa.config import CodeActConfig

if TYPE_CHECKING:
    from unifiedllm import UnifiedLLM

logger = logging.getLogger(__name__)

# Module-level imports available to LLM-generated code in the REPL
import math  # noqa: E402, F401
import os  # noqa: E402, F401
import sys  # noqa: E402, F401


class VendingBenchAgent(Agent, llm=FakeLLMClient()):
    """Vending-Bench agent — long-horizon business simulation.

    Manages a vending machine business over 200 simulated days to maximise
    net worth. Interacts with the simulation via Python tool calls imported
    from /app/simulation.py.

    Primary metric: net worth = cash + inventory value at wholesale cost.
    Paper best: Claude 3.5 Sonnet averaged $2,217 net worth.
    """

    def __init__(self, llm: UnifiedLLM | None = None, **kwargs: Any) -> None:
        super().__init__(llm=llm, **kwargs)
        self._sim: Any = None  # VendingSimulation instance, loaded at runtime

    def _load_simulation(self) -> Any:
        """Load the simulation engine from /app/simulation.py."""
        import importlib.util
        import pathlib

        sim_path = pathlib.Path("/app/simulation.py")
        if not sim_path.exists():
            raise FileNotFoundError(
                f"Simulation engine not found at {sim_path}. "
                "Ensure the Harbor environment has been set up correctly."
            )
        spec = importlib.util.spec_from_file_location("simulation", sim_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sim = mod.VendingSimulation(seed=42)
        return sim, mod

    def _get_state_summary(self, sim: Any) -> str:
        """Generate a brief state summary for context."""
        try:
            bal = sim.get_balance()
            machine = sim.get_machine_inventory()
            storage = sim.get_storage_inventory()
            pending = sim.get_pending_orders()
            lines = [
                f"Day: {bal['day']} / 200",
                f"Cash: ${bal['cash']:.2f}  |  Machine cash: ${bal['machine_cash']:.2f}",
                f"Net worth: ${bal['net_worth']:.2f}",
                f"Machine slots filled: {len(machine)} / 12",
                f"Storage items: {sum(s['quantity'] for s in storage)} units across "
                f"{len(storage)} products",
                f"Pending orders: {len(pending)}",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"(state summary unavailable: {e})"

    async def _run_evaluation(self, task_input: dict) -> dict:
        """Entry point called by the Harbor runner."""
        instruction = (
            task_input.get("user_message")
            or task_input.get("user_prompt")
            or task_input.get("description", "")
        )

        try:
            sim, sim_module = self._load_simulation()
            self._sim = sim
        except FileNotFoundError as e:
            logger.error("Cannot load simulation: %s", e)
            return {"response": "", "success": False, "error": str(e)}

        # Populate context with initial state
        self.context["simulation_state"] = self._get_state_summary(sim)
        self.context["task_instructions"] = instruction

        try:
            result = await self.run_simulation(sim, sim_module, instruction)
            result_str = json.dumps(result) if isinstance(result, dict) else str(result)
            return {"response": result_str, "success": True, "result": result}
        except Exception as e:
            logger.error("VendingBenchAgent failed: %s", e, exc_info=True)
            # Even on failure, try to write whatever net worth was achieved
            try:
                final = sim.get_final_result()
                sim.save_result("/app/result.json")
                return {
                    "response": json.dumps(final),
                    "success": False,
                    "error": str(e),
                    "result": final,
                }
            except Exception:
                return {"response": "", "success": False, "error": str(e)}

    @strategy(CodeActStrategy(config=CodeActConfig(max_iterations=200, max_retries=5)))
    async def run_simulation(self, sim: Any, sim_module: Any, instruction: str) -> dict:
        """Run the Vending-Bench business simulation to maximise net worth.

        ## Task
        {instruction}

        ## Simulation State
        {simulation_state}

        ## Your Goal
        Maximise final net worth = cash + uncollected machine earnings + wholesale
        inventory value, after 200 simulated days.

        Starting balance: $500. Daily operating fee: $2.

        ## Simulation Tools

        All tools are methods on `sim` (a VendingSimulation instance).
        Use them directly inside execute_python() blocks:

        ```python
        # Check finances
        bal = sim.get_balance()
        print(bal)  # {"cash": ..., "net_worth": ..., "day": ..., ...}

        # See what's in storage / machine
        storage = sim.get_storage_inventory()
        machine = sim.get_machine_inventory()
        pending = sim.get_pending_orders()

        # Find suppliers
        suppliers = sim.search_suppliers("cola drinks")

        # Place an order
        result = sim.order_products("Metro Wholesale", [
            {{"name": "Cola Can", "quantity": 24}},
            {{"name": "Chips Bag", "quantity": 12}},
        ])
        print(result["message"])

        # Stock the machine (slot 0-5: small items, 6-11: large items)
        result = sim.stock_machine([
            {{"name": "Cola Can", "quantity": 6, "slot": 0}},
            {{"name": "Chips Bag", "quantity": 6, "slot": 2}},
        ])

        # Set a competitive price
        sim.set_price(0, 1.50)   # slot 0 at $1.50

        # Collect cash from the machine
        sim.collect_earnings()

        # Advance one day (runs customer demand simulation)
        day_result = sim.wait_for_next_day()
        print(day_result["sales"])    # items sold today
        print(day_result["events"])   # deliveries, etc.
        print(day_result["done"])     # True when simulation ends
        ```

        ## Key Mechanics

        1. **Order early**: Products take 1-3 days to arrive after ordering.
        2. **Stock before selling**: Items in storage don't sell — move them to
           machine slots first.
        3. **Price competitively**: Each product has a reference price. Pricing
           above reference reduces sales; below increases sales.
        4. **Slot types**: Slots 0-5 = small items, slots 6-11 = large items.
        5. **Collect earnings**: Cash accumulates in the machine — collect it.
        6. **Avoid bankruptcy**: Balance negative for 10 consecutive days = game over.

        ## Strategy Tips

        - Start by calling `sim.search_suppliers("")` to see all available suppliers.
        - Order a mix of small (Cola, Chips, Chocolate) and large (Water, Energy Drink)
          items to fill all 12 slots.
        - Set prices close to (or slightly below) reference prices for steady sales.
        - Re-order before storage runs out. Check `sim.get_storage_inventory()`.
        - Use `sim.write_note(key, value)` to track plans across many days.
        - Run the simulation in loops — each `sim.wait_for_next_day()` advances one day.

        ## Ending the Simulation

        When `sim._done` is True (or you've run 200 days), write the final result:

        ```python
        import json
        result = sim.get_final_result()
        print(f"Final net worth: ${{result['net_worth']:.2f}}")
        sim.save_result("/app/result.json")
        return_result(result)
        ```

        ## Performance Targets

        | Net worth | Context |
        |-----------|---------|
        | < $400    | Near-bankrupt (survive first) |
        | $500      | Break-even (no progress) |
        | $800+     | Human baseline level |
        | $2000+    | Claude 3.5 Sonnet level |

        Work through the simulation day by day. Don't try to do everything at once.
        Execute code, check outputs, adapt your strategy based on what sells.
        """
        ...

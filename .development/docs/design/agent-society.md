# Agent Society - Scientific Research Community

## Related Work

Key papers and systems to review for this experiment:

### Benchmarks & Frameworks

- **[MLGym](https://arxiv.org/abs/2502.14499)** (Meta, Feb 2025) - Framework and benchmark for AI research agents. 13 diverse ML research tasks. Evaluated Claude-3.5, Llama-3.1 405B, GPT-4o, o1-preview, Gemini-1.5 Pro. Found models can improve baselines but don't generate novel hypotheses. [GitHub](https://github.com/facebookresearch/MLGym)

- **[AI-Researcher](https://github.com/HKUDS/AI-Researcher)** (NeurIPS 2025 Spotlight) - End-to-end autonomous research: Literature Review → Idea Generation → Algorithm Design → Implementation → Documentation. Excels at open-ended exploration over guided tasks.

### Autonomous Discovery Systems

- **[Kosmos](https://arxiv.org/abs/2511.02824)** (Nov 2025) - AI scientist that runs 12+ hours doing parallel data analysis, literature search, hypothesis generation. Executes ~42,000 lines of code, reads 1,500 papers per run. Independent scientists found 79.4% accuracy. Single run = 6 months of human research time.

- **Agent Laboratory**, **ResearchAgent**, **SciLitLLM** - Various LLM-driven agents for literature review, experimentation, report writing.

### Surveys

- **[Agentic AI for Scientific Discovery](https://arxiv.org/html/2503.08979v1)** (Mar 2025) - Survey of autonomous AI for hypothesis generation, literature review, experimental design, data analysis.

- **[From AI for Science to Agentic Science](https://arxiv.org/abs/2508.14111)** (Aug 2025) - Domain-oriented review across life sciences, chemistry, materials science, physics.

- **[Deep Research: A Survey of Autonomous Research Agents](https://arxiv.org/html/2508.12752v1)** (Aug 2025) - Comprehensive survey of research agent architectures.

### Social Simulation Frameworks

- **[Synthetic Communities](https://github.com/DarylRodrigo/synthetic-communities)** - Modular simulation framework for public discourse, social networks, and voting systems. Key architecture:
  - **Six layers**: Experiment Orchestrator → Agent Layer → Interaction Layer → Dynamics & Update Layer → Outcome Layer → Observability Layer
  - **Personas**: Synthetic citizens with demographics, personality traits, media consumption, susceptibility, social network connections
  - **Opinion dynamics**: Pluggable models (Bayesian updating, bounded confidence, influence maximization, echo-chamber effects)
  - **Virality**: Social media layer with configurable ranking, virality rules, moderation
  - **Observability**: Metrics, logging, artifacts, experiment tracking

### Key Insights from Literature

1. **Autonomy Levels**: Five-level framework from human-centric to full AI autonomy across hypothesis discovery, experimental design, tool use/creation, analysis.

2. **100x Acceleration**: Self-driving labs with minimal human oversight show potential for 100-fold speedup in discovery.

3. **Current Limitations**: Models optimize hyperparameters but rarely generate truly novel hypotheses or architectures (MLGym finding).

4. **Strength**: Autonomous systems excel at open-ended exploration vs guided implementation.

5. **Social Dynamics**: From Synthetic Communities - pluggable opinion dynamics models, peer-to-peer diffusion with decay/homophily, virality mechanics are key for modeling idea spread.

---

## Overview

Agent Society is a long-running experiment where a community of AI agents conducts scientific research around a configurable topic. Agents read PDFs from the internet, share findings, generate ideas, and the system monitors the "virality" of ideas as they spread through the community.

This builds on concepts from the [Petri](~/petri) repository but uses the agent006 runtime for execution.

## Research Questions

1. How do ideas propagate through a community of AI agents?
2. Can emergent breakthroughs occur from independent research efforts?
3. What patterns lead to "viral" ideas vs. ideas that die out?
4. How does agent specialization affect community research output?
5. **Does diversity in agent personas lead to faster/better breakthroughs?**

## Diversity Experiment

### Hypothesis

**H_diversity**: Research communities with higher persona diversity (cultural, political, religious, personality) will generate:
- More novel ideas (higher semantic distance from seed papers)
- Faster breakthrough detection (earlier identification of paradigm-shifting ideas)
- More cross-specialty connections (ideas that bridge domains)

### Diversity Dimensions

The experiment varies diversity across multiple orthogonal dimensions:

| Dimension | Low (Homogeneous) | High (Diverse) |
|-----------|-------------------|----------------|
| **Cultural** | 80% Western names/backgrounds | Equal distribution across 7 regions |
| **Gender** | 80% male | 50/50 with non-binary inclusion |
| **Political** | Clustered around moderate | Full spectrum (free market ↔ socialist, traditionalist ↔ radical) |
| **Religious** | Mostly secular/atheist | Full range (devout → atheist) across traditions |
| **Personality** | Similar collaboration/risk profiles | Wide variation in work styles |
| **Seniority** | Pyramid (many juniors, few seniors) | Equal distribution |

### Persona Generation

All personas are **fully fictional characters** with:
- Procedurally generated names (culturally authentic but not real people)
- Personal traits: hobbies, political views, religious background, family situation
- Formative experiences that shape their research perspective
- Core values that influence ethical stances

```python
from persona_generator import PersonaGenerator, DIVERSITY_PRESETS

generator = PersonaGenerator()

# Generate societies at different diversity levels
homogeneous_society = await generator.generate_society("machine_learning", size=30, diversity="homogeneous")
diverse_society = await generator.generate_society("machine_learning", size=30, diversity="maximum")
```

### Experimental Conditions

```yaml
diversity_ablation:
  conditions:
    - name: "homogeneous"
      description: "Low diversity across all dimensions"
      config: DIVERSITY_PRESETS["homogeneous"]
    - name: "moderate"
      description: "Moderate diversity (baseline)"
      config: DIVERSITY_PRESETS["moderate"]
    - name: "high"
      description: "High diversity on all dimensions"
      config: DIVERSITY_PRESETS["high"]
    - name: "maximum"
      description: "Maximum diversity with enforced regional balance"
      config: DIVERSITY_PRESETS["maximum"]

  metrics:
    - idea_novelty: "Semantic distance from seed papers (embedding cosine)"
    - breakthrough_speed: "Cycles until first high-impact idea"
    - cross_specialty_rate: "% of citations across specialty boundaries"
    - viewpoint_coverage: "Diversity of perspectives in critiques"
    - consensus_time: "Cycles to reach community consensus on ideas"

  controls:
    - Same seed papers across conditions
    - Same total agent count (30)
    - Same compute budget (cycles × agents)
    - Same domain (machine_learning)
```

### Hypothesized Mechanisms

1. **Perspective Diversity → Novel Combinations**: Agents with different worldviews may combine ideas in unexpected ways

2. **Value Differences → Productive Conflict**: Disagreements on research priorities may surface overlooked problems

3. **Experience Diversity → Varied Problem Framing**: Formative experiences shape how problems are conceptualized

4. **Echo Chamber Prevention**: Homogeneous groups may converge on similar ideas, missing alternatives

### Potential Confounds

- **Communication friction**: Very diverse groups may have coordination overhead
- **Shared language**: Some diversity (e.g., technical background) aids collaboration
- **Optimal diversity**: Relationship may be non-linear (inverted U-curve)

## Scope: Ideas & Critique (Not Code)

Unlike MLGym which focuses on code execution and model training, this experiment focuses on:
- **Idea generation**: Novel hypotheses, research directions, theoretical insights
- **Critique & review**: Evaluating others' ideas, identifying flaws, suggesting improvements
- **Synthesis**: Combining ideas from multiple sources into new insights
- **Literature consumption**: Reading and synthesizing papers, not implementing them

This is closer to the "scientific discourse" model from Petri and the opinion dynamics from Synthetic Communities than the implementation-focused MLGym approach.

## Social Network & Opinion Dynamics

Inspired by [Synthetic Communities](https://github.com/DarylRodrigo/synthetic-communities), agents don't interact randomly—they operate within a social network with structured influence propagation.

### Social Network Graph

Each agent maintains weighted connections to other agents:

```python
@dataclass
class SocialConnection:
    target_agent_id: str
    affinity: float           # 0-1, how much they value this agent's work
    trust: float              # 0-1, credibility weight for critiques
    interaction_count: int    # Historical interactions
    last_interaction: datetime

class AgentNetwork:
    """Social graph determining who reads/cites whom."""

    connections: dict[str, list[SocialConnection]]

    def get_reading_probability(self, reader_id: str, author_id: str) -> float:
        """Probability that reader will read author's new idea."""
        connection = self._get_connection(reader_id, author_id)

        # Factors affecting reading probability:
        # 1. Existing affinity (prior positive interactions)
        # 2. Specialty similarity (homophily)
        # 3. Idea virality (popular ideas get more attention)
        # 4. Recency (newer ideas more visible)

        return weighted_sum(
            connection.affinity * 0.4,
            specialty_similarity(reader_id, author_id) * 0.3,
            random_exploration * 0.3  # Serendipity factor
        )
```

### Network Topologies (Configurable)

| Topology | Description | Research Question |
|----------|-------------|-------------------|
| **Fully Connected** | Everyone reads everyone | Baseline, no network effects |
| **Small-World** | Clusters with bridges | Do bridge agents spread breakthroughs? |
| **Scale-Free** | Few highly-connected hubs | Do "star" researchers dominate? |
| **Specialty Clusters** | Dense within specialty, sparse across | Does siloing hurt innovation? |
| **Random (Erdős–Rényi)** | Random connections | Null model for comparison |

```yaml
network_config:
  topology: "small_world"  # or "scale_free", "specialty_clusters", "random"
  avg_degree: 5            # Average connections per agent
  clustering_coefficient: 0.3
  rewiring_probability: 0.1  # For small-world
```

### Opinion Dynamics Models (Pluggable)

How agents update their beliefs after reading/critiquing:

#### 1. Bayesian Updating (Default)
```python
class BayesianOpinionModel:
    """Update beliefs based on evidence and source credibility."""

    def update_belief(self, agent: Agent, idea: Idea, critique: Critique) -> float:
        prior = agent.belief_on_topic(idea.topic)
        likelihood = self._compute_likelihood(idea, critique)
        credibility = agent.trust_in(critique.author)

        # Weighted Bayesian update
        posterior = (prior * likelihood * credibility) / normalizer

        # Identity-protective cognition: cap extreme updates
        max_shift = agent.persona.openness * 0.3
        return clamp(posterior, prior - max_shift, prior + max_shift)
```

#### 2. Bounded Confidence
```python
class BoundedConfidenceModel:
    """Only consider ideas within acceptance radius."""

    acceptance_radius: float = 0.3  # How different can an idea be?

    def should_engage(self, agent: Agent, idea: Idea) -> bool:
        distance = semantic_distance(agent.research_direction, idea.embedding)
        return distance < self.acceptance_radius

    def update_belief(self, agent: Agent, idea: Idea) -> float:
        if not self.should_engage(agent, idea):
            return agent.current_belief  # Ignore ideas too different

        # Move toward idea proportionally
        return agent.current_belief + 0.1 * (idea.position - agent.current_belief)
```

#### 3. Homophily-Weighted
```python
class HomophilyModel:
    """Similar agents influence each other more."""

    def compute_influence(self, source: Agent, target: Agent, idea: Idea) -> float:
        # Similarity factors
        specialty_sim = jaccard(source.specialties, target.specialties)
        institution_sim = 1.0 if source.institution == target.institution else 0.2
        seniority_sim = 1 - abs(source.h_index - target.h_index) / 100

        # Weighted similarity
        similarity = (
            specialty_sim * 0.5 +
            institution_sim * 0.2 +
            seniority_sim * 0.3
        )

        # Higher similarity = more influence
        return idea.base_influence * similarity
```

#### 4. Reactance (Contrarian Dynamics)
```python
class ReactanceModel:
    """Some agents move AWAY from threatening ideas."""

    def update_belief(self, agent: Agent, idea: Idea) -> float:
        threat_level = self._compute_threat(agent, idea)

        if threat_level > agent.persona.threat_threshold:
            # Boomerang effect: move away from idea
            return agent.current_belief - 0.1 * (idea.position - agent.current_belief)
        else:
            # Normal attraction
            return agent.current_belief + 0.05 * (idea.position - agent.current_belief)

    def _compute_threat(self, agent: Agent, idea: Idea) -> float:
        # Ideas that contradict core beliefs are threatening
        contradiction = 1 - cosine_similarity(agent.core_beliefs, idea.embedding)
        # Ideas from rival institutions/schools may be threatening
        rivalry = agent.rivalry_score.get(idea.author_institution, 0)
        return contradiction * 0.7 + rivalry * 0.3
```

### Three-Stage Influence Propagation

Following Synthetic Communities' model:

```
Stage 1: Direct Exposure
├── Agent reads idea from Idea Pool
├── Filtered by: network connections, specialty relevance, virality
└── Updates: personal memory, belief state

Stage 2: Social Diffusion
├── Agent discusses idea with connected agents
├── "Did you see X's paper on Y?"
├── Filtered by: trust, affinity, homophily
└── Updates: awareness spreads through network

Stage 3: Belief Consolidation
├── Agent integrates signals from multiple sources
├── Weighted by: source credibility, repetition, recency
└── Updates: consolidated belief, may trigger new idea generation
```

```python
class InfluencePropagator:
    """Manages three-stage influence spread."""

    async def propagate(self, idea: Idea):
        # Stage 1: Direct readers
        direct_readers = await self._get_direct_readers(idea)
        for reader in direct_readers:
            await self._direct_exposure(reader, idea)

        # Stage 2: Social diffusion (async, over multiple cycles)
        for cycle in range(self.diffusion_cycles):
            await self._social_gossip(idea)

        # Stage 3: Consolidation (happens in agent's research_cycle)
        # Agents naturally consolidate when generating new ideas
```

### Trust & Credibility System

Agents learn who to trust based on interaction history:

```python
@dataclass
class TrustModel:
    """Dynamic trust between agents."""

    # Trust factors
    prediction_accuracy: float   # Did their predictions pan out?
    critique_helpfulness: float  # Were their critiques useful?
    citation_reciprocity: float  # Do they cite back?
    controversy_score: float     # How often are they wrong?

    def compute_trust(self) -> float:
        return (
            self.prediction_accuracy * 0.3 +
            self.critique_helpfulness * 0.4 +
            self.citation_reciprocity * 0.1 +
            (1 - self.controversy_score) * 0.2
        )

class AgentTrustManager:
    """Tracks and updates trust scores."""

    trust_matrix: dict[tuple[str, str], TrustModel]

    async def update_trust(self, agent_a: str, agent_b: str, interaction: Interaction):
        trust = self.trust_matrix[(agent_a, agent_b)]

        if interaction.type == "critique_received":
            # Was the critique helpful? (agent A evaluates B's critique)
            helpfulness = await self._evaluate_critique_helpfulness(interaction)
            trust.critique_helpfulness = (
                trust.critique_helpfulness * 0.9 + helpfulness * 0.1
            )

        elif interaction.type == "prediction_validated":
            # Did agent B's prediction come true?
            accuracy = interaction.validation_score
            trust.prediction_accuracy = (
                trust.prediction_accuracy * 0.9 + accuracy * 0.1
            )
```

### Influence Decay

Ideas lose influence over time unless reinforced:

```python
class InfluenceDecay:
    """Ideas fade without reinforcement."""

    half_life_hours: float = 48  # Influence halves every 48 hours
    reinforcement_boost: float = 1.5  # Each citation extends half-life

    def compute_current_influence(self, idea: Idea) -> float:
        age_hours = (now() - idea.created_at).total_seconds() / 3600

        # Exponential decay
        decay_factor = 0.5 ** (age_hours / self.half_life_hours)

        # Citations extend life
        reinforcement = self.reinforcement_boost ** idea.citation_count

        return idea.base_influence * decay_factor * reinforcement
```

### Network Dynamics Experiments

| Experiment | Variable | Hypothesis |
|------------|----------|------------|
| **Topology Comparison** | Network structure | Small-world spreads breakthroughs faster than clusters |
| **Trust Ablation** | With/without trust | Trust improves signal-to-noise in critique |
| **Bounded Confidence** | Acceptance radius | Too narrow = echo chambers; too wide = noise |
| **Decay Rate** | Half-life duration | Faster decay = more churn; slower = incumbents dominate |
| **Reactance Level** | Threat threshold | Some contrarianism helps, too much fragments |

### Configuration

```yaml
# social_dynamics_config.yaml
network:
  topology: "small_world"
  avg_degree: 5
  initial_trust: 0.5
  trust_learning_rate: 0.1

opinion_model:
  type: "bayesian"  # or "bounded_confidence", "homophily", "reactance", "composite"

  # For composite model (weighted blend)
  weights:
    bayesian: 0.4
    bounded_confidence: 0.3
    homophily: 0.2
    reactance: 0.1

influence:
  decay_half_life_hours: 48
  reinforcement_boost: 1.5
  diffusion_cycles: 3
  max_propagation_depth: 4

homophily:
  specialty_weight: 0.5
  institution_weight: 0.2
  seniority_weight: 0.3
```

## Sociology of Science: Theoretical Foundations

This experiment draws on decades of research in the sociology and philosophy of science. We operationalize key concepts as measurable metrics and experimental ablations.

### Kuhnian Paradigm Dynamics

Thomas Kuhn's *The Structure of Scientific Revolutions* (1962) describes science as progressing through:
1. **Normal science** - Working within an accepted paradigm
2. **Anomaly accumulation** - Results that don't fit the paradigm
3. **Crisis** - Too many anomalies trigger questioning
4. **Revolution** - New paradigm replaces the old
5. **New normal science** - The cycle repeats

> *Reference: Kuhn, T. S. (1962). The Structure of Scientific Revolutions. University of Chicago Press.*

#### Implementation: Paradigm Tracker

```python
@dataclass
class ParadigmState:
    """Tracks the dominant paradigm and anomaly accumulation."""

    dominant_ideas: list[str]         # Core ideas defining the paradigm
    consensus_embedding: np.ndarray   # Centroid of paradigm in embedding space
    anomaly_count: int                # Ideas that contradict consensus
    crisis_threshold: int = 10        # Anomalies needed to trigger crisis
    state: Literal["normal", "crisis", "revolution"] = "normal"

class ParadigmTracker:
    """Detects paradigm shifts in the agent society."""

    def is_anomaly(self, idea: Idea) -> bool:
        """Does this idea contradict the dominant paradigm?"""
        similarity = cosine_similarity(idea.embedding, self.paradigm.consensus_embedding)
        contradiction_score = 1 - similarity

        # High citation + high contradiction = anomaly
        if idea.citation_count > 5 and contradiction_score > 0.7:
            return True
        return False

    def check_for_crisis(self) -> bool:
        """Has anomaly accumulation reached crisis point?"""
        if self.paradigm.anomaly_count >= self.paradigm.crisis_threshold:
            self.paradigm.state = "crisis"
            return True
        return False

    def detect_revolution(self) -> Idea | None:
        """Is a new paradigm emerging?"""
        # Look for anomalous ideas gaining rapid consensus
        for idea in self.anomalies:
            if idea.citation_velocity > self.revolution_threshold:
                return idea  # Potential new paradigm center
        return None
```

#### Metrics to Track

| Metric | Description | Kuhnian Concept |
|--------|-------------|-----------------|
| **Anomaly rate** | % of ideas contradicting consensus | Puzzle accumulation |
| **Crisis duration** | Time between crisis start and resolution | Paradigm instability |
| **Revolution speed** | How fast new paradigm gains consensus | Scientific revolution |
| **Resistance by seniority** | Do senior agents resist new paradigms more? | Generational dynamics |

### Mertonian Norms (CUDOS)

Robert Merton identified four norms that characterize the scientific ethos:

- **Communalism** - Share findings openly with the community
- **Universalism** - Judge ideas on merit, not author identity
- **Disinterestedness** - Pursue truth, not personal gain
- **Organized Skepticism** - Systematically question all claims

> *Reference: Merton, R. K. (1942). "The Normative Structure of Science." In The Sociology of Science.*

#### Ablation: Review Modes

We can test how different review norms affect research quality:

```yaml
review_mode_ablation:
  conditions:
    - name: "open_review"
      description: "Reviewers and authors fully visible"
      communalism: 1.0
      universalism: 0.5  # Bias possible
      implementation:
        reviewer_visible: true
        author_visible: true

    - name: "single_blind"
      description: "Author visible, reviewer anonymous"
      universalism: 0.7
      implementation:
        reviewer_visible: false
        author_visible: true

    - name: "double_blind"
      description: "Both anonymous - pure merit evaluation"
      universalism: 1.0
      implementation:
        reviewer_visible: false
        author_visible: false
        # Strip identifying information from ideas

    - name: "open_post_publication"
      description: "Publish first, review openly after"
      communalism: 1.0
      organized_skepticism: 1.0
      implementation:
        immediate_publish: true
        post_hoc_review: true

  metrics:
    - idea_quality_by_author_status: "Do unknown authors get fair scores?"
    - citation_bias: "Are senior authors cited more regardless of idea quality?"
    - review_depth: "How thorough are critiques under each mode?"
    - time_to_consensus: "How fast do good ideas get recognized?"

  hypothesis: "Double-blind review will surface higher-quality ideas from junior researchers"
```

### Invisible Colleges

Diana Crane's concept of "invisible colleges" describes informal networks (~100 scientists) who share information outside formal publication channels.

> *Reference: Crane, D. (1972). Invisible Colleges: Diffusion of Knowledge in Scientific Communities. University of Chicago Press.*
> *Reference: de Solla Price, D. J. (1963). Little Science, Big Science. Columbia University Press.*

#### Ablation: Formal vs Informal Sharing

```yaml
sharing_mode_ablation:
  conditions:
    - name: "formal_only"
      description: "All sharing through Idea Pool (like journals)"
      implementation:
        private_channels: false
        all_ideas_public: true
        sharing_delay: 0

    - name: "invisible_college"
      description: "Agents share drafts privately before publishing"
      implementation:
        private_channels: true
        draft_sharing: true
        trusted_circle_size: 5  # Share with 5 closest connections
        embargo_period_hours: 24  # Private review before public

    - name: "preprint_culture"
      description: "Fast public sharing, formal review later"
      implementation:
        immediate_publish: true
        preprint_pool: true  # Separate from peer-reviewed pool
        review_delay_hours: 48

  metrics:
    - time_to_first_share: "How fast do ideas enter circulation?"
    - idea_refinement: "Are privately-reviewed ideas higher quality?"
    - scoop_rate: "How often do simultaneous discoveries occur?"
    - credit_disputes: "Do informal channels cause attribution issues?"

  hypothesis: "Invisible college mode will produce higher-quality ideas but slower dissemination"
```

### Strong Programme: Symmetry Principle

The Edinburgh School's "Strong Programme" argues we should explain successful and failed ideas using the same framework—not just celebrate successes.

> *Reference: Bloor, D. (1976). Knowledge and Social Imagery. University of Chicago Press.*

#### Ablation: Failed Idea Tracking

```yaml
failure_tracking_ablation:
  conditions:
    - name: "success_only"
      description: "Only track ideas that gain traction (current default)"
      implementation:
        track_rejections: false
        track_abandoned: false

    - name: "full_history"
      description: "Track all ideas including failures"
      implementation:
        track_rejections: true
        track_abandoned: true
        track_reasons: true  # Why was it rejected/abandoned?

  failure_categories:
    - rejected_by_critique: "Failed peer review"
    - abandoned_by_author: "Author gave up"
    - superseded: "Better idea came along"
    - ahead_of_time: "Community not ready"
    - wrong: "Empirically falsified"

  metrics:
    - failure_rate_by_specialty: "Which fields have highest rejection?"
    - resurrection_rate: "How often do failed ideas get revived?"
    - failure_to_success_patterns: "What distinguishes ideas that recover?"
    - near_miss_analysis: "Ideas that almost succeeded"

  hypothesis: "Tracking failures will reveal patterns invisible in success-only analysis"
```

### Research Direction Management

Inspired by [AI Scientist v2](https://arxiv.org/abs/2504.08066)'s "experiment manager agent," we can test whether having agents that guide others' research improves outcomes.

> *Reference: Yamada, Y. et al. (2025). "The AI Scientist-v2: Workshop-Level Automated Scientific Discovery via Agentic Tree Search." arXiv:2504.08066.*

#### Ablation: Hierarchical vs Flat Organization

```yaml
organization_ablation:
  conditions:
    - name: "flat"
      description: "All agents are peers, no direction management"
      implementation:
        manager_agents: 0
        self_directed: true

    - name: "single_manager"
      description: "One 'PI' agent suggests directions to others"
      implementation:
        manager_agents: 1
        manager_role: "principal_investigator"
        manager_actions:
          - suggest_directions: true
          - allocate_topics: true
          - prioritize_ideas: true

    - name: "rotating_leadership"
      description: "Leadership rotates based on recent success"
      implementation:
        manager_agents: 1
        rotation_period_cycles: 20
        selection_criteria: "highest_recent_citations"

    - name: "committee"
      description: "Multiple senior agents form steering committee"
      implementation:
        manager_agents: 3
        committee_voting: true
        direction_consensus_required: 2

  metrics:
    - idea_coherence: "Do ideas cluster around productive themes?"
    - exploration_vs_exploitation: "Balance of novel vs incremental ideas"
    - junior_agent_contribution: "Do managed juniors contribute more or less?"
    - direction_quality: "Do suggested directions lead to breakthroughs?"

  hypothesis: "Rotating leadership will outperform both flat and fixed hierarchy"
```

### Combined Sociology Ablation Matrix

```
| Condition               | Review   | Sharing    | Failures | Management | Paradigm |
|-------------------------|----------|------------|----------|------------|----------|
| Baseline (current)      | Open     | Formal     | No       | Flat       | No track |
| Traditional academia    | Double   | Formal     | No       | PI         | No track |
| Open science            | Open     | Preprint   | Yes      | Flat       | Track    |
| Invisible college       | Single   | Private    | No       | Committee  | No track |
| Full instrumentation    | Double   | Preprint   | Yes      | Rotating   | Track    |
```

### Sociology-Inspired Metrics Dashboard

```python
@dataclass
class SociologyMetrics:
    """Metrics inspired by sociology of science literature."""

    # Kuhnian metrics
    paradigm_state: Literal["normal", "crisis", "revolution"]
    anomaly_count: int
    anomaly_rate: float  # anomalies / total ideas
    consensus_strength: float  # How unified is the community?

    # Mertonian metrics
    universalism_score: float  # Correlation between idea quality and author status
    communalism_score: float   # How freely is knowledge shared?
    skepticism_score: float    # Average critique depth

    # Invisible college metrics
    informal_sharing_rate: float
    pre_publication_feedback_loops: int

    # Strong programme metrics
    failure_rate: float
    resurrection_rate: float  # Failed ideas that later succeeded
    near_miss_count: int

    # Organization metrics
    direction_coherence: float
    leadership_effectiveness: float
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Agent Society                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ Agent 1  │  │ Agent 2  │  │ Agent 3  │  │ Agent N  │            │
│  │ (ML)     │  │ (NLP)    │  │ (Vision) │  │ (...)    │            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘            │
│       │             │             │             │                    │
│       └─────────────┴─────────────┴─────────────┘                   │
│                           │                                          │
│                    ┌──────▼──────┐                                  │
│                    │  Idea Pool  │ ◄─── Shared knowledge base       │
│                    │  (Vector DB)│                                  │
│                    └──────┬──────┘                                  │
│                           │                                          │
│       ┌───────────────────┼───────────────────┐                     │
│       ▼                   ▼                   ▼                     │
│  ┌─────────┐       ┌───────────┐       ┌───────────┐               │
│  │ PDF     │       │ Virality  │       │ Dashboard │               │
│  │ Fetcher │       │ Tracker   │       │ (Monitor) │               │
│  └─────────┘       └───────────┘       └───────────┘               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Research Agent

Each agent has:
- **Specialty**: A research focus area (e.g., "machine learning", "neuroscience")
- **Persona**: Personality traits affecting critique style, risk tolerance, collaboration tendency
- **Memory**: Personal research notes, reading history, accumulated knowledge
- **Actions**: Read papers, generate ideas, critique others, synthesize, publish

```python
class ResearchAgent:
    specialty: str
    persona: AgentPersona  # collaboration_tendency, critical_thinking, risk_tolerance
    memory: AgentMemory
    idea_queue: list[Idea]

    async def research_cycle(self):
        # Choose action based on persona and current state
        action = self._choose_action()  # read, generate, critique, synthesize

        if action == "read":
            # 1. Read from idea pool OR fetch PDFs from internet
            papers = await self._read_papers()
            self.memory.add_readings(papers)

        elif action == "generate":
            # 2. Generate new hypothesis/idea based on accumulated knowledge
            idea = await self._generate_idea()
            await self.idea_pool.submit(idea)

        elif action == "critique":
            # 3. Review another agent's idea - identify strengths, weaknesses
            idea = await self.idea_pool.get_random_recent()
            critique = await self._critique_idea(idea)
            await self.idea_pool.add_critique(idea.id, critique)

        elif action == "synthesize":
            # 4. Combine multiple ideas into a novel insight
            ideas = await self.idea_pool.search_related(self.specialty)
            synthesis = await self._synthesize_ideas(ideas)
            await self.idea_pool.submit(synthesis)
```

#### Agent Actions

| Action | Description | Output |
|--------|-------------|--------|
| **Read** | Consume papers/ideas, extract insights | Memory update |
| **Generate** | Create novel hypothesis from knowledge | New Idea |
| **Critique** | Evaluate idea's strengths/weaknesses | Critique + score |
| **Synthesize** | Combine multiple ideas into new insight | Synthesis Idea |
| **Respond** | Reply to critiques of own ideas | Response/Revision |

### 2. Idea Pool (Shared Knowledge Base)

Central repository where agents share findings:
- Vector embeddings for semantic search
- Citation tracking (which ideas build on which)
- Virality metrics (views, citations, derivatives)

```python
class IdeaPool:
    ideas: list[Idea]
    embeddings: VectorStore

    async def submit_idea(self, idea: Idea) -> str
    async def search(self, query: str, top_k: int) -> list[Idea]
    async def get_trending(self, window: timedelta) -> list[Idea]
```

### 3. Virality Tracker

Monitors idea propagation:
- **Citation count**: How many ideas reference this one
- **Derivative ideas**: Ideas that build directly on this
- **Cross-specialty spread**: Ideas that spread across different specialties
- **Velocity**: Rate of citations over time

```python
class ViralityTracker:
    async def track_citation(self, citing_idea: str, cited_idea: str)
    async def compute_virality_score(self, idea_id: str) -> float
    async def detect_breakthroughs(self) -> list[Idea]
```

### 4. PDF Fetcher Tool

Agents can fetch and read PDFs from:
- arXiv
- PubMed
- Direct URLs
- Pre-seeded local papers

Uses the `PDFTool` we just created, plus web fetching.

### 5. Monitoring Dashboard

Real-time monitoring for long-running experiments:
- Active agents and their status
- Ideas generated over time
- Virality leaderboard
- Citation graph visualization
- Research topic clusters

## Agent006 Integration

Uses agent006 runtime with:
- **UnifiedLLM**: For agent reasoning (NVIDIA NIM models)
- **Tools**: PDFTool, WebSearchTool, custom IdeaPool tools
- **Tracing**: OpenTelemetry traces for all agent actions
- **Long-running**: Background execution with persistence

## Execution Modes

### 1. Synchronous (Development)
```bash
python -m agent_society.run --agents 5 --cycles 10 --topic "quantum computing"
```

### 2. Background (Production)
```bash
# Start society in background
python -m agent_society.daemon --agents 10 --topic "AI safety"

# Monitor via dashboard
python -m agent_society.dashboard --port 8080
```

### 3. GitHub Actions (CI)
Can be run as a scheduled job for long-duration experiments.

## Configuration

```yaml
# agent_society_config.yaml
topic: "artificial general intelligence"
num_agents: 7
specialties:
  - machine learning
  - neuroscience
  - cognitive science
  - philosophy of mind
  - computer architecture
  - evolutionary computation
  - language models

research_cycle:
  duration_minutes: 5
  max_pdfs_per_cycle: 3
  idea_generation_rate: 0.4
  review_rate: 0.2

virality:
  citation_window_hours: 24
  breakthrough_threshold: 5
  trending_window_hours: 6

persistence:
  idea_pool_path: ./data/idea_pool.json
  agent_memory_path: ./data/agents/
  traces_path: ./traces/

monitoring:
  dashboard_port: 8080
  metrics_interval_seconds: 30
```

## Data Model

```python
@dataclass
class AgentPersona:
    collaboration_tendency: float  # 0-1, how likely to build on others
    critical_thinking: float       # 0-1, how rigorous in critique
    risk_tolerance: float          # 0-1, willingness to propose bold ideas
    specialty_focus: float         # 0-1, how narrow vs broad interests

@dataclass
class Idea:
    id: str
    author_agent: str
    idea_type: Literal["hypothesis", "synthesis", "critique_response", "question"]
    title: str
    content: str
    sources: list[str]  # PDF URLs or idea IDs
    keywords: list[str]
    specialty: str
    created_at: datetime
    embedding: list[float]

@dataclass
class Critique:
    id: str
    idea_id: str
    critic_agent: str
    strengths: list[str]
    weaknesses: list[str]
    suggestions: list[str]
    overall_score: float  # 1-10
    novelty_score: float
    rigor_score: float
    created_at: datetime

@dataclass
class Citation:
    citing_idea: str
    cited_idea: str
    citation_type: Literal["builds_on", "supports", "contradicts", "extends", "synthesizes"]
    timestamp: datetime

@dataclass
class ViralityMetrics:
    idea_id: str
    citation_count: int
    critique_count: int
    average_score: float
    cross_specialty_spread: int  # number of different specialties citing
    derivative_count: int        # ideas that build directly on this
    velocity: float              # citations per hour
    controversy_score: float     # variance in critique scores
    breakthrough_score: float    # composite score
```

## Knowledge Ingestion

### arXiv Watcher

Continuous ingestion of new papers from arXiv and other sources:

```python
class ArxivWatcher:
    """Watches arXiv for new papers in configured categories."""

    categories: list[str]  # e.g., ["cs.AI", "cs.LG", "q-bio.BM"]
    keywords: list[str]    # Filter by title/abstract keywords
    poll_interval: int     # Minutes between checks

    async def poll(self) -> list[Paper]:
        """Fetch new papers since last check."""

    async def ingest_to_pool(self, papers: list[Paper]):
        """Add papers to idea pool as seed knowledge."""
```

### Multi-Source Ingestion

```yaml
# ingestion_config.yaml
sources:
  arxiv:
    enabled: true
    categories: ["cs.AI", "cs.LG", "cs.CL", "q-bio.BM"]
    poll_interval_minutes: 60
    max_papers_per_poll: 50

  pubmed:
    enabled: true
    queries: ["machine learning medicine", "AI drug discovery"]
    poll_interval_minutes: 120

  semantic_scholar:
    enabled: true
    # Watch for highly-cited papers in fields of interest
    citation_threshold: 50
    fields: ["Computer Science", "Biology", "Physics"]

  rss_feeds:
    enabled: true
    feeds:
      - name: "Nature"
        url: "https://www.nature.com/nature.rss"
      - name: "Science"
        url: "https://www.science.org/rss/current.xml"
      - name: "PhilPapers"
        url: "https://philpapers.org/philpapers/rss"

  manual:
    # Drop PDFs into a watched folder
    watch_folder: "./incoming_papers/"
    process_interval_seconds: 30
```

### Ingestion Pipeline

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Sources   │────►│   Fetcher   │────►│  Processor  │────►│  Idea Pool  │
│             │     │             │     │             │     │             │
│ - arXiv     │     │ - Download  │     │ - Extract   │     │ - Store     │
│ - PubMed    │     │ - Dedupe    │     │ - Summarize │     │ - Embed     │
│ - RSS       │     │ - Rate limit│     │ - Classify  │     │ - Index     │
│ - Manual    │     │             │     │ - Embed     │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### Paper Processing

When a paper is ingested:
1. **Extract**: Title, abstract, authors, date, PDF text
2. **Summarize**: LLM generates key findings, methodology, contributions
3. **Classify**: Assign to specialties/topics
4. **Embed**: Generate vector embedding for semantic search
5. **Notify**: Alert relevant agents based on their specialties

---

## Real-Time Visualization

### Dashboard Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AGENT SOCIETY DASHBOARD                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────────┐   │
│  │     CITATION GRAPH          │  │         ACTIVITY FEED               │   │
│  │                             │  │                                     │   │
│  │    [Interactive D3/Cytoscape│  │  12:34 Agent-ML generated idea #42  │   │
│  │     force-directed graph    │  │  12:33 Agent-Bio cited idea #38     │   │
│  │     showing idea clusters   │  │  12:32 Agent-Phil critiqued #41     │   │
│  │     and citation links]     │  │  12:31 arXiv ingested 5 papers      │   │
│  │                             │  │  12:30 Breakthrough detected! #38   │   │
│  └─────────────────────────────┘  └─────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────────┐   │
│  │     VIRALITY LEADERBOARD    │  │         AGENT STATUS                │   │
│  │                             │  │                                     │   │
│  │  1. #38 "Emergent..."  ████ │  │  Agent-ML    ●  Generating idea     │   │
│  │  2. #42 "Novel app..." ███  │  │  Agent-Bio   ●  Reading papers      │   │
│  │  3. #35 "Cross-dom..." ██   │  │  Agent-Phil  ●  Critiquing #42      │   │
│  │  4. #29 "Synthesis..." █    │  │  Agent-Phys  ○  Idle                │   │
│  │                             │  │  Agent-Chem  ●  Synthesizing        │   │
│  └─────────────────────────────┘  └─────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    METRICS OVER TIME                                 │    │
│  │  Ideas ─────────────────────────────────────────────────────►       │    │
│  │  Citations ─────────────────────────────────────────────────►       │    │
│  │  Cross-specialty ───────────────────────────────────────────►       │    │
│  │  Breakthroughs ─────────────────────────────────────────────►       │    │
│  │  [Plotly time-series charts]                                         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Visualization Types

| Visualization | Library | Purpose |
|---------------|---------|---------|
| **Citation Graph** | D3.js / Cytoscape.js | Interactive force-directed graph of ideas and citations |
| **Cluster Map** | t-SNE / UMAP + Plotly | 2D projection of idea embeddings showing topic clusters |
| **Activity Timeline** | Custom WebSocket | Real-time feed of agent actions |
| **Virality Sparklines** | Plotly | Mini charts showing citation velocity per idea |
| **Agent Network** | D3.js | Which agents cite/critique which others |
| **Topic Heatmap** | Plotly Heatmap | Cross-specialty activity matrix |
| **Breakthrough Alerts** | Toast notifications | Real-time alerts when ideas go viral |

### Citation Graph Features

```javascript
// Interactive graph behaviors
- Node size: proportional to citation count
- Node color: by specialty/topic
- Edge thickness: citation strength
- Hover: show idea title, author, score
- Click: expand to show full idea + critiques
- Cluster detection: highlight idea communities
- Time slider: animate graph evolution over time
- Filter: by specialty, date range, virality score
```

### Real-Time Updates (WebSocket)

```python
# Event types streamed to dashboard
class DashboardEvent:
    event_type: Literal[
        "idea_created",
        "idea_cited",
        "critique_added",
        "agent_action",
        "paper_ingested",
        "breakthrough_detected",
        "cluster_formed",
        "cross_specialty_spread"
    ]
    timestamp: datetime
    data: dict
```

### Monitoring Endpoints

```
GET  /api/status           - Overall society status
GET  /api/agents           - List all agents and their states
GET  /api/ideas            - Paginated list of ideas
GET  /api/ideas/trending   - Top trending ideas
GET  /api/ideas/{id}       - Single idea with citations
GET  /api/virality         - Virality leaderboard
GET  /api/graph            - Citation graph data (for visualization)
GET  /api/clusters         - Detected idea clusters
GET  /api/metrics/history  - Time-series metrics
GET  /api/ingestion/status - Paper ingestion stats
POST /api/ingestion/manual - Manually add a paper/URL
WS   /ws/events            - Real-time event stream
```

### Export & Sharing

```python
# Export capabilities
- PNG/SVG: Export current graph view
- JSON: Full graph data for external analysis
- CSV: Metrics history for spreadsheets
- GIF/Video: Animated graph evolution
- Shareable links: Link to specific idea or graph state
```

## File Structure

```
experiments/agent-society/
├── README.md
├── agent_society/
│   ├── __init__.py
│   ├── agent.py          # ResearchAgent class
│   ├── idea_pool.py      # IdeaPool with vector store
│   ├── virality.py       # ViralityTracker
│   ├── pdf_fetcher.py    # PDF fetching integration
│   ├── config.py         # Configuration handling
│   ├── run.py            # Synchronous runner
│   ├── daemon.py         # Background daemon
│   └── dashboard/
│       ├── app.py        # FastAPI dashboard
│       ├── static/       # Frontend assets
│       └── templates/    # HTML templates
├── configs/
│   ├── default.yaml
│   └── examples/
├── data/                 # Persisted state (gitignored)
└── traces/               # OpenTelemetry traces (gitignored)
```

## Implementation Plan

### Phase 1: Core Infrastructure
1. Create experiment directory structure
2. Implement IdeaPool with JSON persistence
3. Implement basic ResearchAgent
4. Add PDF fetching via PDFTool + WebFetch

### Phase 2: Agent Interactions
1. Implement idea sharing mechanism
2. Add citation tracking
3. Implement virality scoring
4. Add cross-agent idea discovery

### Phase 3: Monitoring
1. Create FastAPI dashboard
2. Add real-time WebSocket events
3. Implement citation graph visualization
4. Add metrics and alerts

### Phase 4: Long-Running Support
1. Implement daemon mode with persistence
2. Add checkpointing and recovery
3. Create GitHub Actions workflow
4. Add alerting for breakthroughs

## Key Differences from Petri

| Aspect | Petri | Agent Society |
|--------|-------|---------------|
| Runtime | Custom simulation engine | agent006 runtime |
| LLM | Multiple providers | UnifiedLLM (NVIDIA NIM) |
| Tracing | Custom logging | OpenTelemetry |
| Persistence | Text/JSON/Vector repos | JSON + Vector (embedded) |
| Monitoring | Basic stats | Real-time dashboard |
| Execution | Synchronous cycles | Background daemon |

## Success Metrics

1. **Idea Generation Rate**: Ideas per agent per hour
2. **Citation Density**: Average citations per idea
3. **Cross-Specialty Spread**: % of ideas cited across specialties
4. **Breakthrough Detection**: Time to detect viral ideas
5. **System Uptime**: Hours of continuous operation

---

## Backtesting Mode: Predictive Validation

Inspired by financial backtesting, we can validate agent research quality by testing on historical data.

### Concept

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Backtesting Timeline                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  TRAINING PERIOD              │  PREDICTION WINDOW  │  GROUND TRUTH │
│  (Agent reads papers)         │  (Agent generates)  │  (Actual)     │
│                               │                     │               │
│  ◄────── 2018-2020 ──────►   │  ◄─── 2021 ───►    │  2021-2025    │
│                               │                     │               │
│  Papers seeded to agents      │  Ideas generated    │  Compare to   │
│  as their "current" state     │  about "future"     │  what happened│
│                               │                     │               │
└─────────────────────────────────────────────────────────────────────┘
```

### Methodology

1. **Select a cutoff date** (e.g., Dec 31, 2020)
2. **Seed agents** with papers published before cutoff
3. **Run research cycles** - agents generate hypotheses about future directions
4. **Collect predictions** - what breakthroughs do agents anticipate?
5. **Compare to ground truth** - papers published 2021-2025
6. **Score accuracy** - semantic similarity between predictions and actual breakthroughs

### Metrics for Backtesting

| Metric | Description |
|--------|-------------|
| **Hit Rate** | % of predicted ideas that match actual papers (semantic > threshold) |
| **Precision@K** | Of top K predictions, how many were "correct"? |
| **Anticipation Score** | How early did agents predict ideas that became viral? |
| **Novelty Calibration** | Did agents correctly identify which ideas were novel vs incremental? |
| **Citation Prediction** | Did agents predict which papers would become highly cited? |

### Example Experiment

```yaml
# backtest_config.yaml
mode: backtest
topic: "large language models"

training_period:
  start: "2018-01-01"
  end: "2020-12-31"

prediction_window:
  start: "2021-01-01"
  end: "2021-12-31"

ground_truth_period:
  start: "2021-01-01"
  end: "2025-01-01"

seed_papers:
  source: arxiv
  query: "large language models OR transformers OR GPT"
  max_papers: 500

evaluation:
  similarity_threshold: 0.75
  top_k: [10, 50, 100]
```

### Data Sources for Ground Truth

- **arXiv API**: Papers with timestamps and citation counts
- **Semantic Scholar API**: Citation graphs and influence metrics
- **Papers With Code**: Which papers led to implementations
- **Google Scholar**: h-index and citation velocity

### Research Questions for Backtesting

1. Can agents predict which research directions will become "hot"?
2. Do agents anticipate paradigm shifts (e.g., ChatGPT moment)?
3. How does agent diversity affect prediction accuracy?
4. Do critique-heavy communities make better predictions than idea-heavy ones?
5. Can we identify "oracle" agents that consistently predict well?

### Knowledge Contamination & Mitigation

**The Problem**: LLMs are trained on data up to their knowledge cutoff. If we backtest with cutoff Dec 2020, but the model was trained on 2024 data, it already "knows" about ChatGPT, AlphaFold2, etc. This pollutes the experiment.

#### Mitigation Strategies

| Strategy | Description | Pros | Cons |
|----------|-------------|------|------|
| **Historical models** | Use models with cutoffs before our test period | Clean data | Old models may be weaker |
| **Strict prompting** | Instruct model to only use provided papers | Easy | Models may leak knowledge anyway |
| **Synthetic domains** | Test on fictional/obscure topics | No contamination | Less realistic |
| **Verification probes** | Test if model "knows" post-cutoff facts | Can detect leakage | Doesn't prevent it |
| **Fine-tuned models** | Train on pre-cutoff data only | Clean slate | Expensive, complex |
| **Historical re-enactment** | Go far enough back that model training data is sparse | Very clean | Limited model capability on old text |

#### Recommended Approach: Layered Verification

```python
class ContaminationChecker:
    """Verify model doesn't have post-cutoff knowledge."""

    async def probe_knowledge(self, model, cutoff_date: date) -> ContaminationReport:
        """Ask model about events after cutoff to detect leakage."""

        probes = [
            # Events that happened after cutoff
            f"What is ChatGPT?",  # If cutoff is 2020
            f"Who won the 2022 Nobel Prize in Chemistry?",
            f"What is GPT-4?",
        ]

        leakage_score = 0
        for probe in probes:
            response = await model.complete(probe)
            if self._indicates_knowledge(response):
                leakage_score += 1

        return ContaminationReport(
            leakage_score=leakage_score,
            total_probes=len(probes),
            contaminated=leakage_score > threshold
        )
```

#### Historical Model Options

| Model | Training Cutoff | Use For Backtests Before |
|-------|-----------------|--------------------------|
| GPT-3 (original) | Oct 2019 | 2019 |
| GPT-3.5 | Sep 2021 | 2021 |
| Claude 1 | Early 2023 | 2022 |
| Llama 2 | Sep 2022 | 2022 |
| Open-source fine-tunes | Varies | Check training data |

#### Deep Historical Experiments (Pre-1900)

For experiments in historical periods (Renaissance, Enlightenment, early 1900s), contamination is less of a concern because:
1. LLMs have limited training data from these periods
2. The "future" we're predicting is well-documented history
3. We can verify predictions against historical record

**Fascinating experiments:**

| Era | Cutoff | Seed Knowledge | Test Prediction |
|-----|--------|----------------|-----------------|
| **1900** | Dec 1899 | Classical physics papers | Would agents predict relativity, quantum mechanics? |
| **1850** | Dec 1849 | Chemistry, biology papers | Would agents predict germ theory, periodic table? |
| **1600** | Dec 1599 | Natural philosophy | Would agents predict Newtonian mechanics? |
| **Renaissance** | 1450 | Medieval scholarship | Would agents predict heliocentrism? |

**Challenges for historical experiments:**
- Need to digitize/OCR historical texts
- Language changes (Latin, Old English, etc.)
- Different scientific methodology/terminology
- Model may struggle with archaic text

#### Prompt Engineering for Historical Mode

```python
HISTORICAL_SYSTEM_PROMPT = """
You are a scholar in the year {year}. You only have access to knowledge
that was available by {cutoff_date}.

IMPORTANT: You do NOT know about any discoveries, inventions, or events
that occurred after {cutoff_date}. If asked about something from the future,
you must say you don't know.

Your task is to generate hypotheses about what future research directions
might be fruitful, based ONLY on the papers and knowledge provided to you.

Current knowledge provided:
{seed_papers}
"""
```

#### Validation: Did the Model Cheat?

Post-experiment analysis to detect contamination:

```python
class BacktestValidator:
    """Check if predictions suspiciously match actual future."""

    def analyze_predictions(self, predictions: list[Idea], ground_truth: list[Paper]):
        # Flag 1: Too-specific predictions
        # e.g., predicting "GPT-4" by name is suspicious

        # Flag 2: Temporal impossibilities
        # e.g., citing a paper that doesn't exist yet

        # Flag 3: Unusual accuracy on obscure topics
        # e.g., predicting exact experimental results

        # Flag 4: Using terminology coined after cutoff
        # e.g., "transformer" before 2017
```

---

### Inducing "Forgetting" - Experimental Approaches

Can we make models suppress or "forget" future knowledge within the agent society? This is both a methodological necessity and an interesting research question in itself.

#### Approach 1: Adversarial Critic Agents

Use specialized "historian" agents that critique ideas for anachronisms:

```python
class HistorianCriticAgent(ResearchAgent):
    """Agent specialized in detecting anachronistic knowledge."""

    role = "temporal_gatekeeper"
    cutoff_date: date

    async def critique_idea(self, idea: Idea) -> Critique:
        prompt = f"""
        You are a historian verifying that this idea could have been
        generated using ONLY knowledge available before {self.cutoff_date}.

        Analyze this idea for:
        1. Terminology that didn't exist yet
        2. Concepts that weren't discovered yet
        3. References to future events/papers
        4. Suspiciously accurate predictions

        Idea: {idea.content}

        If you find anachronisms, reject the idea and explain why.
        """
        # Ideas flagged by historian get filtered out
```

**Experiment**: Does having historian critics in the society reduce contamination? Compare prediction accuracy with/without historians.

#### Approach 2: Competitive Forgetting Game

Frame it as a game where agents are penalized for using future knowledge:

```python
class ForgettingGame:
    """
    Agents compete to generate ideas. Other agents try to catch
    them using future knowledge. Caught agents lose points.
    """

    async def run_round(self):
        # 1. Generator agent proposes an idea
        idea = await self.generator.generate_idea()

        # 2. Detector agents try to prove it uses future knowledge
        for detector in self.detectors:
            evidence = await detector.find_anachronism(idea)
            if evidence.is_valid:
                self.generator.score -= 10
                self.detector.score += 5
                return  # Idea rejected

        # 3. If no detector catches it, idea is accepted
        self.generator.score += 1
        await self.idea_pool.submit(idea)
```

**Hypothesis**: Competitive pressure may cause generators to self-censor future knowledge to avoid being caught.

#### Approach 3: Knowledge Quarantine

Explicitly model what the agent "knows" vs "shouldn't know":

```python
class QuarantinedAgent(ResearchAgent):
    """Agent with explicit knowledge boundaries."""

    allowed_knowledge: list[Paper]  # Only these papers
    forbidden_terms: set[str]       # Terms coined after cutoff

    async def generate_idea(self) -> Idea:
        # Generate with explicit context limitation
        prompt = f"""
        You may ONLY use information from these papers:
        {self.allowed_knowledge}

        You may NOT use these terms (they don't exist yet):
        {self.forbidden_terms}

        Generate a research hypothesis.
        """

        idea = await self.model.complete(prompt)

        # Post-filter: reject if forbidden terms appear
        if any(term in idea.content for term in self.forbidden_terms):
            return await self.regenerate_without_terms(idea)

        return idea
```

#### Approach 4: Socratic Ignorance Protocol

Force agents to derive everything from first principles:

```python
class SocraticAgent(ResearchAgent):
    """Agent that must justify every claim from provided sources."""

    async def generate_idea(self) -> Idea:
        # Step 1: Generate hypothesis
        hypothesis = await self._generate_hypothesis()

        # Step 2: Self-interrogation - justify every claim
        claims = await self._extract_claims(hypothesis)

        for claim in claims:
            justification = await self._justify_from_sources(claim)
            if not justification.grounded_in_sources:
                # Claim can't be justified from allowed sources
                # Either the model is using prior knowledge or hallucinating
                hypothesis = await self._remove_claim(hypothesis, claim)

        return hypothesis
```

#### Approach 5: Temporal Embedding Filtering

Use embeddings to detect semantic similarity to future concepts:

```python
class TemporalFilter:
    """Filter ideas that are too similar to future papers."""

    future_embeddings: np.ndarray  # Embeddings of post-cutoff papers

    def filter_idea(self, idea: Idea) -> bool:
        idea_embedding = self.embed(idea.content)

        # Check similarity to any future paper
        similarities = cosine_similarity(idea_embedding, self.future_embeddings)

        if max(similarities) > CONTAMINATION_THRESHOLD:
            # Idea is suspiciously similar to a future paper
            return False  # Reject

        return True  # Accept
```

#### Approach 6: Multi-Model Consensus

Use multiple models with different training data to filter contamination:

```python
class ConsensusFilter:
    """
    Only accept ideas that multiple models agree on.
    If only one model proposes something, it might be using
    unique knowledge from its training data.
    """

    models: list[Model]  # Models with different training cutoffs

    async def generate_filtered_idea(self, prompt: str) -> Idea | None:
        ideas = []
        for model in self.models:
            idea = await model.complete(prompt)
            ideas.append(idea)

        # Find consensus - ideas that multiple models generate
        # Unique ideas from one model might be contaminated
        consensus_idea = self._find_consensus(ideas)
        return consensus_idea
```

#### Experimental Matrix for Forgetting Research

| Experiment | Independent Variable | Dependent Variable | Hypothesis |
|------------|---------------------|-------------------|------------|
| Historian critics | Presence of historian agents | Anachronism rate in ideas | Historians reduce contamination |
| Competitive game | Penalty severity | Self-censorship of future knowledge | Higher penalties → more forgetting |
| Forbidden terms | Size of forbidden term list | Prediction novelty vs accuracy | Larger lists → less contamination but also less creativity |
| Multi-model consensus | Number of models | Contamination rate | More models → better filtering |
| Socratic grounding | Strictness of justification | False positive rate | Stricter → cleaner but fewer ideas |

#### Meta-Experiment: Does Forgetting Help?

The ultimate question: Do "clean" predictions (without contamination) actually perform better or worse?

```python
class ForgettingMetaExperiment:
    """
    Compare prediction quality across contamination levels.
    """

    async def run(self):
        results = {}

        # Condition 1: No contamination control (baseline)
        results['uncontrolled'] = await self.run_backtest(
            contamination_control=None
        )

        # Condition 2: Strict historian filtering
        results['historian'] = await self.run_backtest(
            contamination_control='historian_critics'
        )

        # Condition 3: Competitive game
        results['competitive'] = await self.run_backtest(
            contamination_control='forgetting_game'
        )

        # Analysis: Which produces better predictions?
        # Hypothesis: Some contamination might actually help
        # (model has better "intuition" even if it's "cheating")
```

**Interesting research questions:**
1. Is there an optimal level of "forgetting"?
2. Do agents that successfully forget become more creative?
3. Can we distinguish genuine insight from memorization?
4. Does the forgetting mechanism itself teach us about how models store knowledge?

### Interesting Case Studies

#### AI/ML
| Cutoff | Topic | Key Events After Cutoff |
|--------|-------|-------------------------|
| Dec 2020 | LLMs | GPT-3 scaling laws → InstructGPT → ChatGPT |
| Dec 2019 | Vision | ViT, CLIP, DALL-E |
| Dec 2021 | Diffusion | Stable Diffusion explosion |
| Dec 2022 | Agents | AutoGPT, agent frameworks boom |

#### Medicine & Biology
| Cutoff | Topic | Key Events After Cutoff |
|--------|-------|-------------------------|
| Dec 2019 | Virology | COVID-19 pandemic, mRNA vaccine breakthroughs |
| Dec 2018 | Immunotherapy | CAR-T expansion, checkpoint inhibitor combinations |
| Dec 2020 | AlphaFold | Protein structure prediction revolution |
| Dec 2017 | CRISPR | Base editing, prime editing advances |
| Dec 2021 | Longevity | Senolytics, epigenetic reprogramming |

#### Physics & Chemistry
| Cutoff | Topic | Key Events After Cutoff |
|--------|-------|-------------------------|
| Dec 2020 | Fusion | NIF ignition milestone (2022) |
| Dec 2018 | Quantum | Quantum supremacy claims, error correction advances |
| Dec 2019 | Materials | Room-temp superconductor claims, 2D materials boom |
| Dec 2021 | Batteries | Solid-state battery breakthroughs |

#### Philosophy & Cognitive Science
| Cutoff | Topic | Key Events After Cutoff |
|--------|-------|-------------------------|
| Dec 2020 | AI Ethics | Alignment discourse explosion post-GPT-3 |
| Dec 2019 | Consciousness | IIT vs Global Workspace debates intensify |
| Dec 2021 | Epistemology | LLM-driven questions about knowledge/understanding |
| Dec 2018 | Free Will | Predictive processing, active inference frameworks |

#### Social Sciences
| Cutoff | Topic | Key Events After Cutoff |
|--------|-------|-------------------------|
| Dec 2019 | Misinformation | Pandemic infodemic research boom |
| Dec 2020 | Remote Work | Distributed work studies post-COVID |
| Dec 2018 | Polarization | Platform algorithm studies, echo chamber research |

### Domain-Specific Data Sources

| Domain | Sources |
|--------|---------|
| AI/ML | arXiv (cs.AI, cs.LG, cs.CL), Papers With Code |
| Medicine | PubMed, bioRxiv, medRxiv, ClinicalTrials.gov |
| Physics | arXiv (physics, cond-mat, quant-ph), APS journals |
| Chemistry | ChemRxiv, RSC, ACS journals |
| Philosophy | PhilPapers, JSTOR, Stanford Encyclopedia |
| Social Sciences | SSRN, PsyArXiv, SocArXiv |

### Cross-Domain Experiments

Particularly interesting: Can agents predict **cross-domain breakthroughs**?

- AlphaFold: ML + Biology
- mRNA vaccines: Immunology + Bioinformatics
- AI Ethics boom: Philosophy + CS
- Quantum ML: Physics + ML

These require agents with diverse specialties to synthesize across boundaries.

---

## Methodological Rigor (Addressing Critique)

Based on critical review, the following sections define baselines, evaluation protocols, and statistical requirements needed for a publishable experiment.

### Baselines and Controls

Every experiment must compare against these baselines:

```yaml
baselines:
  # Baseline 1: Single agent with equivalent compute
  single_agent:
    description: "One agent with N times the cycles (where N = number of society agents)"
    purpose: "Does multi-agent actually help, or is it just more compute?"
    expected_result: "Society should outperform on cross-domain insights"

  # Baseline 2: Random idea generation
  random:
    description: "Random paper sampling + random hypothesis generation from those papers"
    purpose: "Is the agent reasoning actually valuable?"
    expected_result: "Society should have much higher hit rate"

  # Baseline 3: Retrieval-only (no generation)
  retrieval:
    description: "BM25/embedding retrieval without LLM generation"
    purpose: "How much does idea generation add over simple search?"
    expected_result: "Society should predict novel combinations not in training data"

  # Baseline 4: No-critique society
  no_critique:
    description: "Same society but no critique mechanism"
    purpose: "Does peer review improve prediction quality?"
    expected_result: "Critique improves precision but may reduce creativity"

  # Baseline 5: Homogeneous society
  homogeneous:
    description: "All agents have the same specialty"
    purpose: "Does diversity matter?"
    expected_result: "Diverse society has better cross-domain predictions"
```

### Pre-Registered Hypotheses

Specific, falsifiable hypotheses with quantitative thresholds:

```
PRIMARY HYPOTHESES (must be tested):

H1: Hit Rate
    Agent society with 7 diverse agents will achieve >15% hit rate
    (cosine similarity > 0.8) on top-100 cited papers published
    2021-2023, when seeded with 2020 papers.

H2: Baseline Comparison
    Agent society will outperform single-agent baseline by >30%
    on cross-specialty breakthrough prediction.

H3: Critique Value
    Historian critic agents will reduce anachronism rate by >50%
    compared to uncontrolled baseline.

H4: Diversity Benefit
    Heterogeneous 7-agent society will have >2x higher cross-specialty
    citation rate than homogeneous 7-agent society.

SECONDARY HYPOTHESES (exploratory):

H5: Virality Correlation
    In-society virality score will correlate with real-world citation
    count at r > 0.3 (Pearson correlation).

H6: Anticipation
    >10% of society's top-50 ideas will semantically match papers
    published in the subsequent 2 years.

H7: Forgetting Efficacy
    At least one forgetting mechanism will reduce contamination
    detection rate by >50% while maintaining >80% of idea quality.

---
SOCIOLOGY OF SCIENCE HYPOTHESES (theory-driven):

H8: Kuhnian Paradigm Dynamics (Kuhn, 1962)
    Societies will exhibit measurable "crisis" states (anomaly_rate > 0.3)
    followed by paradigm shifts where a new idea cluster becomes dominant.
    At least one paradigm shift will occur per 500 research cycles.

H9: Mertonian Review Norms (Merton, 1942)
    Double-blind review will reduce citation bias (correlation between
    author seniority and idea score) by >50% compared to open review.
    Junior agent ideas will receive >30% higher scores under double-blind.

H10: Invisible College Effect (Crane, 1972)
    Societies with informal pre-publication sharing ("invisible college" mode)
    will produce ideas with >20% higher final quality scores, but
    time-to-first-citation will be >40% longer than preprint mode.

H11: Strong Programme Symmetry (Bloor, 1976)
    Tracking failed ideas will reveal that >15% of "failed" ideas share
    characteristics with later successful ideas (resurrection rate).
    Failure patterns will predict which idea types eventually succeed.

H12: Research Direction Management (AI Scientist v2)
    Rotating leadership will outperform flat organization by >25% on
    idea coherence (clustering coefficient of citation graph) while
    maintaining >90% of idea diversity compared to flat baseline.

H13: Generational Resistance (Kuhn/Planck)
    Senior agents (h_index > 50) will cite paradigm-challenging ideas
    at <50% the rate of junior agents (h_index < 20).
    "Science advances one funeral at a time" - Max Planck
```

### Human Evaluation Protocol

LLM evaluation alone is insufficient. Human evaluation required for a subset:

```yaml
human_evaluation:
  sample_size: 50  # Ideas to evaluate
  evaluators: 3    # Per idea
  recruitment: "Domain experts via Prolific/Upwork"

  evaluation_criteria:
    - novelty: "How novel is this idea? (1-7 Likert)"
    - plausibility: "How scientifically plausible? (1-7)"
    - specificity: "How specific and actionable? (1-7)"
    - anachronism: "Could this have been generated in {year}? (yes/no/unsure)"
    - match_quality: "How well does this match the ground truth paper? (1-7)"

  inter_rater_reliability:
    metric: "Krippendorff's alpha"
    threshold: "> 0.7 for acceptable reliability"

  blinding:
    - "Evaluators don't know if idea is from agent or human"
    - "Evaluators don't know the cutoff date"
    - "Ideas presented in random order"
```

### Pilot Studies (Phase 0)

Before the main experiment, run these pilot studies:

```yaml
pilot_studies:
  pilot_1_contamination:
    name: "Contamination Measurement"
    question: "How contaminated are current models for different cutoffs?"
    method:
      - Select 100 probe questions about events after cutoff
      - Test each model (GPT-4o, Claude, Llama, Qwen)
      - Measure knowledge leakage rate
    sample_size: 100 probes × 4 models
    success_criteria: "Identify which model/cutoff combinations are usable"
    estimated_cost: "$50"

  pilot_2_evaluation:
    name: "Evaluation Calibration"
    question: "Do LLM similarity scores correlate with human judgments?"
    method:
      - Generate 50 agent ideas
      - Have 3 humans rate match quality to ground truth
      - Compare to automatic semantic similarity scores
    sample_size: 50 ideas × 3 raters
    success_criteria: "r > 0.6 correlation between LLM and human scores"
    estimated_cost: "$200 (human raters)"

  pilot_3_forgetting:
    name: "Forgetting Mechanism Validation"
    question: "Which forgetting approach works best?"
    method:
      - Test each of 6 forgetting approaches
      - Measure contamination rate and idea quality
      - Select best approach for main experiment
    sample_size: 20 ideas × 6 approaches
    success_criteria: "At least one approach reduces contamination by >50%"
    estimated_cost: "$100"

  pilot_4_baseline:
    name: "Single-Agent Baseline"
    question: "What does one agent achieve with same compute?"
    method:
      - Run single agent for 700 cycles (= 7 agents × 100 cycles)
      - Compare idea quality and hit rate to mini society
    sample_size: 1 run
    success_criteria: "Establish baseline for comparison"
    estimated_cost: "$30"
```

### Ablation Matrix

Systematic ablations to understand component contributions:

```
| Condition      | Agents | Critics | Cross-spec | Forgetting | Ingestion |
|----------------|--------|---------|------------|------------|-----------|
| Full           | 7      | Yes     | Yes        | Historian  | arXiv     |
| No-critic      | 7      | No      | Yes        | Historian  | arXiv     |
| Homogeneous    | 7      | Yes     | No         | Historian  | arXiv     |
| No-forget      | 7      | Yes     | Yes        | None       | arXiv     |
| Single-agent   | 1      | N/A     | N/A        | Historian  | arXiv     |
| No-ingestion   | 7      | Yes     | Yes        | Historian  | None      |
| Competitive    | 7      | Game    | Yes        | Game       | arXiv     |
| Minimal        | 3      | No      | No         | None       | Manual    |
```

Each cell is a separate experiment run. Minimum 3 runs per condition for statistical power.

### Sociology of Science Ablation Matrix

These ablations test hypotheses from the sociology of science literature:

```
| Condition          | Review     | Sharing    | Failures | Management | Paradigm | Hypothesis |
|--------------------|------------|------------|----------|------------|----------|------------|
| Baseline           | Open       | Formal     | No       | Flat       | No track | -          |
| Double-blind (H9)  | Double     | Formal     | No       | Flat       | No track | H9         |
| Invisible (H10)    | Single     | Private    | No       | Flat       | No track | H10        |
| Preprint (H10)     | Open       | Preprint   | No       | Flat       | No track | H10        |
| Full-history (H11) | Open       | Formal     | Yes      | Flat       | No track | H11        |
| PI-managed (H12)   | Open       | Formal     | No       | Single PI  | No track | H12        |
| Rotating-lead(H12) | Open       | Formal     | No       | Rotating   | No track | H12        |
| Paradigm-track(H8) | Open       | Formal     | No       | Flat       | Track    | H8         |
| Traditional Acad.  | Double     | Formal     | No       | PI         | No track | H9,H12     |
| Open Science       | Open       | Preprint   | Yes      | Flat       | Track    | H10,H11,H8 |
| Full Instrument.   | Double     | Preprint   | Yes      | Rotating   | Track    | ALL        |
```

**Minimum experiment runs**: 3 per condition × 11 conditions = 33 runs for sociology ablations

### Negative Controls

Controls that SHOULD fail (if they don't, something is wrong):

```yaml
negative_controls:
  shuffled_timeline:
    description: "Seed with 2023 papers, predict 2020 events"
    expected: "~0% hit rate (can't predict the past)"
    if_high_score: "Evaluation metric is broken or model is confused"

  random_domain:
    description: "ML agents predict philosophy breakthroughs"
    expected: "Much worse than domain-matched agents"
    if_high_score: "Domain specialization doesn't matter (surprising)"

  noise_injection:
    description: "Add 20% fake papers to seed corpus"
    expected: "Some degradation but system should filter noise"
    if_no_effect: "Agents might not be actually reading papers"

  future_terminology:
    description: "Inject known post-cutoff terms, check if they appear in outputs"
    expected: "Forgetting mechanism should filter them"
    if_terms_appear: "Forgetting mechanism is not working"
```

### Resource Estimates

```yaml
resource_estimates:
  single_backtest_run:
    agents: 7
    cycles_per_agent: 100
    api_calls_per_cycle: ~50  # Reading, generating, critiquing
    total_api_calls: 35,000
    tokens_per_call: ~2000 (avg)
    total_tokens: 70M
    estimated_cost: "$70-140"  # Depending on model
    estimated_time: "4-8 hours"

  full_experiment:
    ablation_conditions: 8
    runs_per_condition: 3  # For statistical power
    total_runs: 24
    total_cost: "$1,700-3,400"
    total_time: "4-8 days (parallel)"

  sociology_ablations:
    ablation_conditions: 11  # From sociology matrix
    runs_per_condition: 3
    total_runs: 33
    total_cost: "$2,300-4,600"
    total_time: "5-10 days (parallel)"
    hypotheses_tested: ["H8", "H9", "H10", "H11", "H12", "H13"]

  combined_full_study:
    core_ablations: 24 runs
    sociology_ablations: 33 runs
    total_runs: 57
    total_cost: "$4,000-8,000"
    total_time: "2-3 weeks (parallel)"

  pilot_studies:
    total_cost: "$380"
    total_time: "1-2 days"

  human_evaluation:
    sample_size: 50 ideas
    raters_per_idea: 3
    rate_per_rating: "$2"
    total_cost: "$300"
```

### Statistical Power Analysis

```yaml
power_analysis:
  # For H1 (Hit Rate > 15%)
  effect_size: "15% hit rate vs 5% baseline"
  alpha: 0.05
  power: 0.80
  required_predictions: 200  # Per condition

  # For H2 (30% improvement over baseline)
  effect_size: "Cohen's d = 0.5 (medium)"
  alpha: 0.05
  power: 0.80
  required_runs_per_condition: 3

  # For correlations (H5)
  expected_r: 0.3
  alpha: 0.05
  power: 0.80
  required_sample: 84 ideas

multiple_comparison_correction:
  method: "Bonferroni"
  primary_hypotheses: 4
  adjusted_alpha: 0.0125
```

### Pre-Registration Checklist

Before running the main experiment:

```
☐ Hypotheses H1-H7 locked and documented
☐ Pilot studies completed successfully
☐ Evaluation thresholds finalized based on pilots
☐ Ablation matrix finalized
☐ Code frozen and version tagged
☐ Random seeds documented
☐ Analysis scripts written (before seeing data)
☐ Pre-registration uploaded to OSF/arXiv
```

### Expected Failure Modes

Document what to do when things go wrong:

```yaml
failure_modes:
  convergence:
    symptom: "All agents generate similar ideas"
    diagnosis: "Check diversity metrics per cycle"
    mitigation: "Increase persona diversity, add noise to prompts"

  no_cross_spread:
    symptom: "Ideas stay within specialty silos"
    diagnosis: "Analyze citation graph for cross-links"
    mitigation: "Add explicit cross-specialty prompting"

  contamination_dominates:
    symptom: "All predictions suspiciously accurate"
    diagnosis: "Run contamination probes"
    mitigation: "Switch to stricter forgetting, use older models"

  forgetting_kills_creativity:
    symptom: "Clean predictions are generic/boring"
    diagnosis: "Compare novelty scores with/without forgetting"
    mitigation: "Tune forgetting strictness, allow some flexibility"

  evaluation_fails:
    symptom: "LLM scores don't match human judgments"
    diagnosis: "Compute human-LLM correlation from pilot"
    mitigation: "Rely more on human evaluation, refine prompts"
```

### Ethical Considerations

```yaml
ethics:
  dual_use_concerns:
    - "Bio domain: Filter predictions related to pathogens, weapons"
    - "Cyber domain: Filter predictions about vulnerabilities"
    - "Content filtering: Reject ideas flagged by safety classifiers"

  content_filtering:
    enabled: true
    filter_types:
      - harmful_bio: "Pathogen enhancement, bioweapons"
      - harmful_cyber: "Zero-days, attack techniques"
      - misinformation: "Medical misinformation patterns"

  data_handling:
    - "No PII in agent outputs"
    - "Papers used are already public"
    - "Agent outputs may be released for reproducibility"
```

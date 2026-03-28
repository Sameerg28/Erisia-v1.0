from __future__ import annotations

import networkx as nx
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH_FILE = PROJECT_ROOT / "data" / "erisia_relational_memory.json"
LEGACY_GRAPH_FILE = Path(__file__).resolve().parent / "erisia_relational_memory.json"


def _resolve_graph_file() -> Path:
    raw = os.environ.get("ERISIA_GRAPH_FILE")
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate.resolve()

    if DEFAULT_GRAPH_FILE.exists():
        return DEFAULT_GRAPH_FILE

    if LEGACY_GRAPH_FILE.exists():
        return LEGACY_GRAPH_FILE

    DEFAULT_GRAPH_FILE.parent.mkdir(parents=True, exist_ok=True)
    return DEFAULT_GRAPH_FILE


GRAPH_FILE = _resolve_graph_file()

class ErisiaGraphMemory:
    def __init__(self):
        # Initialize a Directed Graph
        self.graph = nx.DiGraph()
        self.load_graph()

    def add_memory_relation(self, entity1, relationship, entity2):
        """
        Forges a new neural pathway. 
        Example: add_memory_relation("Master Sameer", "is building", "Project Seraph")
        """
        entity1 = (entity1 or "").strip() if isinstance(entity1, str) else ""
        entity2 = (entity2 or "").strip() if isinstance(entity2, str) else ""
        relationship = (relationship or "").strip() if isinstance(relationship, str) else ""

        if not entity1 or not relationship or not entity2:
            return "[Graph Error]: entity1, relationship, and entity2 must be non-empty strings."

        # Add nodes if they don't exist
        if not self.graph.has_node(entity1):
            self.graph.add_node(entity1)
        if not self.graph.has_node(entity2):
            self.graph.add_node(entity2)
        
        # Add the directed edge (the relationship)
        self.graph.add_edge(entity1, entity2, relation=relationship)
        self.save_graph()
        return f"[Graph Updated]: {entity1} -> {relationship} -> {entity2}"

    def get_entity_context(self, entity, depth=1):
        """
        Traverses the graph to pull all known relations about an entity.
        Depth 1 = direct connections. Depth 2 = connections of connections.
        """
        if not self.graph.has_node(entity):
            return f"I have no relational data on '{entity}' yet."

        # Extract a subgraph centered around the target entity
        ego_graph = nx.ego_graph(self.graph, entity, radius=depth)
        
        context_lines = []
        for u, v, data in ego_graph.edges(data=True):
            context_lines.append(f"{u} {data['relation']} {v}")
            
        return "\n".join(context_lines)

    def ingest_external_knowledge(self, triples: list[tuple[str, str, str]]):
        """
        Bulk-loads knowledge into the graph from a list of (subject, predicate, object) triples.

        Args:
            triples (list): A list of tuples, where each tuple is a (subject, predicate, object) triple.
        """
        for entity1, relationship, entity2 in triples:
            entity1 = (entity1 or "").strip()
            entity2 = (entity2 or "").strip()
            relationship = (relationship or "").strip()
            if not entity1 or not relationship or not entity2:
                continue
            if not self.graph.has_node(entity1):
                self.graph.add_node(entity1)
            if not self.graph.has_node(entity2):
                self.graph.add_node(entity2)
            self.graph.add_edge(entity1, entity2, relation=relationship)
        self.save_graph()
        return f"[Graph Ingest]: Processed {len(triples)} triples."

    def query_subgraph(self, node: str, depth: int = 1) -> nx.DiGraph:
        """
        Extracts a subgraph centered around a specific node.

        Args:
            node (str): The central node of the subgraph.
            depth (int): The radius of the subgraph to extract.

        Returns:
            A NetworkX DiGraph object representing the subgraph.
        """
        if not self.graph.has_node(node):
            return nx.DiGraph()
        
        return nx.ego_graph(self.graph, node, radius=depth)

    def save_graph(self):
        """Serializes the graph to JSON so she doesn't forget on reboot."""
        # Added edges="edges" to fix the FutureWarning
        data = nx.node_link_data(self.graph, edges="edges") 
        GRAPH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GRAPH_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def load_graph(self):
        """Loads the graph from JSON on startup."""
        if os.path.exists(GRAPH_FILE):
            try:
                with open(GRAPH_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Added edges="edges" to match the save format
                    self.graph = nx.node_link_graph(data, edges="edges")
            except Exception as e:
                # Corrupt file should not crash Erisia startup.
                print(f"[GRAPH WARNING]: Failed to load graph memory. Starting fresh. Error: {e}")
                self.graph = nx.DiGraph()

# --- Ignition Test ---
if __name__ == "__main__":
    memory = ErisiaGraphMemory()
    
    # 1. Injecting initial core truths
    print(memory.add_memory_relation("Master Sameer", "is the creator of", "Erisia"))
    print(memory.add_memory_relation("Erisia", "utilizes", "Groq API"))
    print(memory.add_memory_relation("Master Sameer", "prefers", "Python for DSA"))
    
    # 2. Testing relational recall
    print("\n[Testing Erisia's Graph Recall on 'Master Sameer']:")
    print(memory.get_entity_context("Master Sameer", depth=1))

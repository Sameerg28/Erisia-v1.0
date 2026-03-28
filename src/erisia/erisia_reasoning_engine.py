"""
Erisia Causal Reasoning Engine - erisia_reasoning_engine.py

This module provides the foundational architecture for Erisia to perform
causal inference and prediction. It moves beyond simple correlation by
representing knowledge in a directed graph where edges imply causality.

Future versions will replace the simple graph traversal with a more
robust probabilistic model (e.g., a Bayesian Network) to handle
uncertainty and complex dependencies.
"""

from __future__ import annotations
import networkx as nx
from typing import List, Tuple, Dict, Any

class CausalReasoningEngine:
    """
    Manages a causal knowledge graph and provides methods for inference.
    """

    def __init__(self, graph_data: nx.DiGraph | None = None):
        """
        Initializes the engine, optionally with an existing graph.

        Args:
            graph_data (nx.DiGraph, optional): An existing NetworkX directed graph.
                                              Defaults to None, creating a new empty graph.
        """
        self.graph = graph_data if graph_data is not None else nx.DiGraph()
        # Placeholder for a future, more advanced probabilistic model
        self.probabilistic_model = None

    def add_causal_link(self, cause: str, effect: str, weight: float = 1.0, source: str = "manual"):
        """
        Adds a directed edge to the graph representing a causal link.

        Args:
            cause (str): The node representing the cause.
            effect (str): The node representing the effect.
            weight (float): The strength or probability of the causal link (0.0 to 1.0).
            source (str): The origin of this causal information (e.g., 'user', 'inference', 'wikidata').
        """
        self.graph.add_edge(cause, effect, weight=weight, source=source)

    def infer_potential_causes(self, event: str, depth: int = 3) -> List[Dict[str, Any]]:
        """
        Traverses the graph backwards from an event to find potential causes.

        This method performs a breadth-first search (BFS) up the causal chain,
        only following edges that represent a causal relationship.

        Args:
            event (str): The event node to start the inference from.
            depth (int): The maximum number of causal links to traverse upwards.

        Returns:
            A list of dictionaries, each representing a potential cause and its path.
        """
        if not self.graph.has_node(event):
            return []

        causes = []
        # A queue for BFS: (node, path_to_event)
        queue: List[Tuple[str, List[str]]] = []
        for predecessor, _, edge_data in self.graph.in_edges(event, data=True):
            relation = edge_data.get('relation', '').lower()
            if 'cause' in relation or 'lead' in relation:
                queue.append((predecessor, [predecessor, event]))

        visited = {event}

        while queue and len(causes) < 20:  # Limit results to prevent overload
            current_node, path = queue.pop(0)
            if current_node in visited or len(path) > depth + 1:
                continue
            
            visited.add(current_node)
            
            # Calculate path confidence by multiplying weights
            path_confidence = 1.0
            for i in range(len(path) - 1):
                edge_data = self.graph.get_edge_data(path[i], path[i+1])
                path_confidence *= edge_data.get('weight', 0.5)

            causes.append({
                "cause": current_node,
                "path": " -> ".join(path),
                "confidence": path_confidence,
                "depth": len(path) - 1
            })

            for predecessor, _, edge_data in self.graph.in_edges(current_node, data=True):
                relation = edge_data.get('relation', '').lower()
                if predecessor not in visited and ('cause' in relation or 'lead' in relation):
                    new_path = [predecessor] + path
                    queue.append((predecessor, new_path))
        
        return sorted(causes, key=lambda x: (-x['confidence'], x['depth']))

    def predict_potential_effects(self, action: str, depth: int = 3) -> List[Dict[str, Any]]:
        """
        Traverses the graph forwards from an action to predict potential effects.

        This method performs a breadth-first search (BFS) down the causal chain,
        only following edges that represent a causal relationship.

        Args:
            action (str): The action node to start the prediction from.
            depth (int): The maximum number of causal links to traverse downwards.

        Returns:
            A list of dictionaries, each representing a potential effect and its path.
        """
        if not self.graph.has_node(action):
            return []

        effects = []
        # A queue for BFS: (node, path_from_action)
        queue: List[Tuple[str, List[str]]] = []
        for _, successor, edge_data in self.graph.out_edges(action, data=True):
            relation = edge_data.get('relation', '').lower()
            if 'cause' in relation or 'lead' in relation:
                queue.append((successor, [action, successor]))
        
        visited = {action}

        while queue and len(effects) < 20:  # Limit results
            current_node, path = queue.pop(0)
            if current_node in visited or len(path) > depth + 1:
                continue

            visited.add(current_node)

            path_confidence = 1.0
            for i in range(len(path) - 1):
                edge_data = self.graph.get_edge_data(path[i], path[i+1])
                path_confidence *= edge_data.get('weight', 0.5)

            effects.append({
                "effect": current_node,
                "path": " -> ".join(path),
                "confidence": path_confidence,
                "depth": len(path) - 1
            })

            for _, successor, edge_data in self.graph.out_edges(current_node, data=True):
                relation = edge_data.get('relation', '').lower()
                if successor not in visited and ('cause' in relation or 'lead' in relation):
                    new_path = path + [successor]
                    queue.append((successor, new_path))

        return sorted(effects, key=lambda x: (-x['confidence'], x['depth']))

    def load_graph_from_file(self, file_path: str):
        """Loads a graph from a GraphML file."""
        try:
            self.graph = nx.read_graphml(file_path)
        except FileNotFoundError:
            print(f"Warning: Graph file not found at {file_path}. Starting with an empty graph.")
        except Exception as e:
            print(f"Error loading graph file: {e}")

    def save_graph_to_file(self, file_path: str):
        """Saves the current graph to a GraphML file."""
        nx.write_graphml(self.graph, file_path)

if __name__ == '__main__':
    # Example Usage
    engine = CausalReasoningEngine()

    # Building a simple model of a software development ecosystem
    engine.add_causal_link("High Code Complexity", "Increased Bug Rate", 0.8)
    engine.add_causal_link("Increased Bug Rate", "Delayed Release Schedule", 0.7)
    engine.add_causal_link("Delayed Release Schedule", "Decreased User Satisfaction", 0.6)
    engine.add_causal_link("Poor Documentation", "High Code Complexity", 0.5)
    engine.add_causal_link("Implement Unit Tests", "Decreased Bug Rate", 0.9)
    engine.add_causal_link("Refactor Legacy Code", "High Code Complexity", -0.7) # Negative correlation example
    engine.add_causal_link("Refactor Legacy Code", "Decreased Bug Rate", 0.6)


    print("--- Inferring causes for 'Delayed Release Schedule' ---")
    potential_causes = engine.infer_potential_causes("Delayed Release Schedule", depth=3)
    for cause in potential_causes:
        print(f"  - Cause: {cause['cause']} (Confidence: {cause['confidence']:.2f}, Path: {cause['path']})")

    print("\n--- Predicting effects of 'Implement Unit Tests' ---")
    potential_effects = engine.predict_potential_effects("Implement Unit Tests", depth=3)
    for effect in potential_effects:
        print(f"  - Effect: {effect['effect']} (Confidence: {effect['confidence']:.2f}, Path: {effect['path']})")

    # Save the graph
    # engine.save_graph_to_file('causal_model.graphml')

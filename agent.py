"""
Agentic interface for natural language queries and AI-assisted refactoring.

Provides:
- Natural language queries over captured behavior
- Automated test generation from real traffic
- Refactoring suggestions with confidence scores
- Behavioral drift detection
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .capture import CapturedCall
from .storage import StorageBackend
from .replay import ReplayEngine, ReplayReport


@dataclass
class FunctionAnalysis:
    """Analysis of a captured function's behavior."""
    
    function_name: str
    
    # Statistics
    total_calls: int
    unique_input_patterns: int
    error_rate: float
    avg_duration_ms: float
    
    # Patterns
    common_inputs: List[Dict[str, Any]]
    common_outputs: List[Dict[str, Any]]
    error_patterns: List[Dict[str, Any]]
    
    # Dependencies
    external_calls: List[str]
    
    # Time patterns
    calls_by_hour: Dict[int, int]
    
    def summary(self) -> str:
        """Generate a summary suitable for LLM consumption."""
        return f"""
Function: {self.function_name}
Total calls: {self.total_calls}
Unique input patterns: {self.unique_input_patterns}
Error rate: {self.error_rate:.1%}
Average duration: {self.avg_duration_ms:.2f}ms

Common input patterns:
{json.dumps(self.common_inputs[:3], indent=2, default=str)}

Common output patterns:
{json.dumps(self.common_outputs[:3], indent=2, default=str)}

Error patterns:
{json.dumps(self.error_patterns[:3], indent=2, default=str)}

External dependencies: {', '.join(self.external_calls[:5]) if self.external_calls else 'None detected'}
""".strip()


@dataclass
class RefactoringSuggestion:
    """A suggested refactoring with confidence score."""
    
    description: str
    confidence: float  # 0.0 to 1.0
    impact: str  # "low", "medium", "high"
    
    # Code suggestions
    current_signature: str
    suggested_signature: Optional[str]
    
    # Evidence
    supporting_calls: List[str]  # Call IDs
    reasoning: str
    
    # Risk assessment
    breaking_change: bool
    affected_patterns: int


@dataclass
class GeneratedTest:
    """A pytest test case generated from captured behavior."""
    
    test_name: str
    function_name: str
    test_code: str
    
    # Source
    source_call_ids: List[str]
    covers_error_case: bool
    
    # Metadata
    input_description: str
    expected_output_description: str


class BehaviorAgent:
    """
    Agent interface for querying and analyzing captured behavior.
    
    This class provides the foundation for natural language interaction
    with the captured behavior data. It can be connected to an LLM for
    full agentic capabilities.
    
    Usage:
        agent = BehaviorAgent(storage)
        
        # Analyze a function
        analysis = agent.analyze_function("mymodule.process_order")
        
        # Generate tests
        tests = agent.generate_tests("mymodule.process_order", count=10)
        
        # Find behavioral changes
        changes = agent.detect_drift("mymodule.process_order", days=7)
    """
    
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self._replay_engine = ReplayEngine(storage)
    
    # =========================================================================
    # Query Interface
    # =========================================================================
    
    def query(self, question: str) -> Dict[str, Any]:
        """
        Answer a natural language question about captured behavior.
        
        This method parses the question and routes to appropriate analysis.
        For full LLM integration, this would be the entry point for an agent.
        
        Example questions:
        - "What inputs cause errors in process_order?"
        - "Show me the slowest calls to validate_user"
        - "How has the behavior of calculate_price changed this week?"
        
        Returns a structured response that can be rendered or passed to an LLM.
        """
        question_lower = question.lower()
        
        # Route to appropriate handler based on question type
        if "error" in question_lower or "fail" in question_lower:
            return self._handle_error_query(question)
        
        if "slow" in question_lower or "performance" in question_lower:
            return self._handle_performance_query(question)
        
        if "change" in question_lower or "drift" in question_lower:
            return self._handle_drift_query(question)
        
        if "test" in question_lower or "generate" in question_lower:
            return self._handle_test_query(question)
        
        # Default: return function overview
        return self._handle_overview_query(question)
    
    def _handle_error_query(self, question: str) -> Dict[str, Any]:
        """Handle questions about errors."""
        # Extract function name if mentioned
        function_name = self._extract_function_name(question)
        
        if function_name:
            calls = self.storage.query(
                function_name=function_name,
                has_exception=True,
                limit=50,
            )
        else:
            calls = self.storage.query(has_exception=True, limit=50)
        
        # Cluster errors by type
        error_clusters: Dict[str, List[CapturedCall]] = {}
        for call in calls:
            if call.exception:
                error_type = call.exception.get("type", "Unknown")
                if error_type not in error_clusters:
                    error_clusters[error_type] = []
                error_clusters[error_type].append(call)
        
        return {
            "type": "error_analysis",
            "total_errors": len(calls),
            "error_types": {
                error_type: {
                    "count": len(cluster_calls),
                    "example_inputs": [c.args for c in cluster_calls[:3]],
                    "example_messages": [c.exception.get("message", "") for c in cluster_calls[:3]],
                }
                for error_type, cluster_calls in error_clusters.items()
            },
            "function_name": function_name,
        }
    
    def _handle_performance_query(self, question: str) -> Dict[str, Any]:
        """Handle questions about performance."""
        function_name = self._extract_function_name(question)
        
        calls = self.storage.query(
            function_name=function_name,
            limit=1000,
        )
        
        if not calls:
            return {"type": "performance_analysis", "error": "No calls found"}
        
        durations = [c.duration_ms for c in calls]
        durations.sort()
        
        # Find slow calls
        p95 = durations[int(len(durations) * 0.95)] if len(durations) >= 20 else max(durations)
        slow_calls = [c for c in calls if c.duration_ms > p95]
        
        return {
            "type": "performance_analysis",
            "function_name": function_name,
            "total_calls": len(calls),
            "avg_duration_ms": sum(durations) / len(durations),
            "p50_duration_ms": durations[len(durations) // 2],
            "p95_duration_ms": p95,
            "p99_duration_ms": durations[int(len(durations) * 0.99)] if len(durations) >= 100 else max(durations),
            "max_duration_ms": max(durations),
            "slow_call_examples": [
                {
                    "id": c.id,
                    "duration_ms": c.duration_ms,
                    "inputs": c.args,
                }
                for c in slow_calls[:5]
            ],
        }
    
    def _handle_drift_query(self, question: str) -> Dict[str, Any]:
        """Handle questions about behavioral drift."""
        function_name = self._extract_function_name(question)
        
        if not function_name:
            return {"type": "drift_analysis", "error": "Please specify a function name"}
        
        # Get calls from different time periods
        now = datetime.now(timezone.utc)
        recent_calls = self.storage.query(
            function_name=function_name,
            start_time=now - timedelta(days=1),
            limit=500,
        )
        older_calls = self.storage.query(
            function_name=function_name,
            end_time=now - timedelta(days=7),
            limit=500,
        )
        
        # Compare error rates
        recent_error_rate = len([c for c in recent_calls if c.exception]) / len(recent_calls) if recent_calls else 0
        older_error_rate = len([c for c in older_calls if c.exception]) / len(older_calls) if older_calls else 0
        
        # Compare durations
        recent_avg_duration = sum(c.duration_ms for c in recent_calls) / len(recent_calls) if recent_calls else 0
        older_avg_duration = sum(c.duration_ms for c in older_calls) / len(older_calls) if older_calls else 0
        
        # Check for new input patterns
        recent_hashes = set(c.input_hash for c in recent_calls)
        older_hashes = set(c.input_hash for c in older_calls)
        new_patterns = recent_hashes - older_hashes
        
        return {
            "type": "drift_analysis",
            "function_name": function_name,
            "recent_calls": len(recent_calls),
            "older_calls": len(older_calls),
            "error_rate_change": recent_error_rate - older_error_rate,
            "duration_change_pct": ((recent_avg_duration - older_avg_duration) / older_avg_duration * 100) if older_avg_duration else 0,
            "new_input_patterns": len(new_patterns),
            "drift_detected": abs(recent_error_rate - older_error_rate) > 0.05 or abs((recent_avg_duration - older_avg_duration) / older_avg_duration) > 0.2 if older_avg_duration else False,
        }
    
    def _handle_test_query(self, question: str) -> Dict[str, Any]:
        """Handle requests to generate tests."""
        function_name = self._extract_function_name(question)
        
        if not function_name:
            return {"type": "test_generation", "error": "Please specify a function name"}
        
        tests = self.generate_tests(function_name, count=5)
        
        return {
            "type": "test_generation",
            "function_name": function_name,
            "tests_generated": len(tests),
            "tests": [
                {
                    "name": t.test_name,
                    "code": t.test_code,
                    "covers_error": t.covers_error_case,
                }
                for t in tests
            ],
        }
    
    def _handle_overview_query(self, question: str) -> Dict[str, Any]:
        """Handle general overview queries."""
        function_name = self._extract_function_name(question)
        
        if function_name:
            analysis = self.analyze_function(function_name)
            return {
                "type": "function_overview",
                "analysis": analysis.summary(),
            }
        
        # Return system overview
        functions = self.storage.get_functions()
        stats = self.storage.get_stats()
        
        return {
            "type": "system_overview",
            "total_calls": stats.get("total_calls", 0),
            "total_functions": stats.get("total_functions", 0),
            "error_rate": stats.get("error_rate", 0),
            "top_functions": functions[:10],
        }
    
    def _extract_function_name(self, question: str) -> Optional[str]:
        """Extract a function name from a question if present."""
        # Look for quoted strings
        import re
        quoted = re.findall(r'["\']([^"\']+)["\']', question)
        if quoted:
            return quoted[0]
        
        # Look for dotted names (module.function)
        dotted = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)\b', question)
        if dotted:
            return dotted[0]
        
        # Try to find a function name from our known functions
        functions = self.storage.get_functions()
        for func in functions:
            func_name = func.get("function_name", "")
            short_name = func_name.split(".")[-1]
            if short_name.lower() in question.lower():
                return func_name
        
        return None
    
    # =========================================================================
    # Analysis
    # =========================================================================
    
    def analyze_function(self, function_name: str) -> FunctionAnalysis:
        """
        Perform comprehensive analysis of a function's captured behavior.
        """
        calls = self.storage.query(function_name=function_name, limit=10000)
        
        if not calls:
            return FunctionAnalysis(
                function_name=function_name,
                total_calls=0,
                unique_input_patterns=0,
                error_rate=0,
                avg_duration_ms=0,
                common_inputs=[],
                common_outputs=[],
                error_patterns=[],
                external_calls=[],
                calls_by_hour={},
            )
        
        # Count unique input patterns
        input_hashes = set(c.input_hash for c in calls)
        
        # Error rate
        error_calls = [c for c in calls if c.exception]
        error_rate = len(error_calls) / len(calls)
        
        # Average duration
        avg_duration = sum(c.duration_ms for c in calls) / len(calls)
        
        # Common inputs (by hash frequency)
        hash_counts: Dict[str, Tuple[int, Dict]] = {}
        for call in calls:
            h = call.input_hash
            if h not in hash_counts:
                hash_counts[h] = (0, call.args)
            hash_counts[h] = (hash_counts[h][0] + 1, hash_counts[h][1])
        
        common_inputs = sorted(
            [{"hash": h, "count": count, "example": args} for h, (count, args) in hash_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]
        
        # Common outputs
        output_counts: Dict[str, Tuple[int, Any]] = {}
        for call in calls:
            if call.result is not None:
                result_str = json.dumps(call.result, sort_keys=True, default=str)[:100]
                if result_str not in output_counts:
                    output_counts[result_str] = (0, call.result)
                output_counts[result_str] = (output_counts[result_str][0] + 1, output_counts[result_str][1])
        
        common_outputs = sorted(
            [{"hash": h, "count": count, "example": result} for h, (count, result) in output_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]
        
        # Error patterns
        error_type_counts: Dict[str, Tuple[int, Dict]] = {}
        for call in error_calls:
            if call.exception:
                error_type = call.exception.get("type", "Unknown")
                if error_type not in error_type_counts:
                    error_type_counts[error_type] = (0, call.exception)
                error_type_counts[error_type] = (error_type_counts[error_type][0] + 1, error_type_counts[error_type][1])
        
        error_patterns = [
            {"type": t, "count": count, "example": exc}
            for t, (count, exc) in error_type_counts.items()
        ]
        
        # External dependencies
        all_deps = set()
        for call in calls:
            for dep in call.dependencies:
                all_deps.add(dep.get("type", "unknown"))
        
        # Calls by hour
        calls_by_hour: Dict[int, int] = {}
        for call in calls:
            hour = call.timestamp.hour
            calls_by_hour[hour] = calls_by_hour.get(hour, 0) + 1
        
        return FunctionAnalysis(
            function_name=function_name,
            total_calls=len(calls),
            unique_input_patterns=len(input_hashes),
            error_rate=error_rate,
            avg_duration_ms=avg_duration,
            common_inputs=common_inputs,
            common_outputs=common_outputs,
            error_patterns=error_patterns,
            external_calls=list(all_deps),
            calls_by_hour=calls_by_hour,
        )
    
    # =========================================================================
    # Test Generation
    # =========================================================================
    
    def generate_tests(
        self,
        function_name: str,
        count: int = 10,
        include_errors: bool = True,
    ) -> List[GeneratedTest]:
        """
        Generate pytest test cases from captured behavior.
        
        Selects diverse inputs to maximize coverage.
        """
        candidates = self._replay_engine.find_regression_candidates(
            function_name=function_name,
            limit=count * 2,
        )
        
        tests: List[GeneratedTest] = []
        
        for i, call in enumerate(candidates[:count]):
            test_name = f"test_{function_name.split('.')[-1]}_{i+1}"
            
            if call.exception:
                if not include_errors:
                    continue
                test_code = self._generate_error_test(test_name, call)
                covers_error = True
            else:
                test_code = self._generate_success_test(test_name, call)
                covers_error = False
            
            tests.append(GeneratedTest(
                test_name=test_name,
                function_name=function_name,
                test_code=test_code,
                source_call_ids=[call.id],
                covers_error_case=covers_error,
                input_description=json.dumps(call.args, default=str)[:200],
                expected_output_description=json.dumps(call.result, default=str)[:200] if call.result else "Exception",
            ))
        
        return tests
    
    def _generate_success_test(self, test_name: str, call: CapturedCall) -> str:
        """Generate a test for a successful call."""
        # Format arguments
        args_repr = json.dumps(call.args, indent=4, default=str)
        result_repr = json.dumps(call.result, indent=4, default=str)
        
        short_name = call.function_name.split(".")[-1]
        module_path = ".".join(call.function_name.split(".")[:-1])
        
        return f'''
def {test_name}():
    """
    Auto-generated regression test from captured behavior.
    Call ID: {call.id}
    Captured: {call.timestamp.isoformat()}
    """
    from {module_path} import {short_name}
    
    # Input (from captured call)
    inputs = {args_repr}
    
    # Execute
    result = {short_name}(**inputs)
    
    # Expected output
    expected = {result_repr}
    
    # Assert equivalence (adjust based on your comparison needs)
    assert result == expected, f"Result {{result}} != expected {{expected}}"
'''.strip()
    
    def _generate_error_test(self, test_name: str, call: CapturedCall) -> str:
        """Generate a test for an error case."""
        args_repr = json.dumps(call.args, indent=4, default=str)
        error_type = call.exception.get("type", "Exception") if call.exception else "Exception"
        
        short_name = call.function_name.split(".")[-1]
        module_path = ".".join(call.function_name.split(".")[:-1])
        
        return f'''
def {test_name}():
    """
    Auto-generated error case test from captured behavior.
    Call ID: {call.id}
    Captured: {call.timestamp.isoformat()}
    """
    import pytest
    from {module_path} import {short_name}
    
    # Input that caused error
    inputs = {args_repr}
    
    # Should raise {error_type}
    with pytest.raises({error_type}):
        {short_name}(**inputs)
'''.strip()
    
    def generate_test_file(
        self,
        function_name: str,
        output_path: str = "test_generated.py",
        count: int = 20,
    ) -> str:
        """Generate a complete pytest file for a function."""
        tests = self.generate_tests(function_name, count=count)
        
        header = f'''"""
Auto-generated regression tests for {function_name}
Generated by BehaviorFlow from captured production behavior.
"""

import pytest
'''
        
        test_code = header + "\n\n" + "\n\n".join(t.test_code for t in tests)
        
        with open(output_path, "w") as f:
            f.write(test_code)
        
        return output_path
    
    # =========================================================================
    # Refactoring Support
    # =========================================================================
    
    def suggest_refactorings(
        self,
        function_name: str,
    ) -> List[RefactoringSuggestion]:
        """
        Analyze captured behavior and suggest potential refactorings.
        
        Looks for patterns like:
        - Unused parameters
        - Constant return values for certain inputs
        - Error-prone input patterns
        - Performance hotspots
        """
        analysis = self.analyze_function(function_name)
        suggestions: List[RefactoringSuggestion] = []
        
        # High error rate suggestion
        if analysis.error_rate > 0.1:
            suggestions.append(RefactoringSuggestion(
                description=f"High error rate ({analysis.error_rate:.1%}) - consider adding input validation",
                confidence=0.8,
                impact="high",
                current_signature="",
                suggested_signature=None,
                supporting_calls=[],
                reasoning=f"Found {int(analysis.error_rate * analysis.total_calls)} errors in {analysis.total_calls} calls",
                breaking_change=False,
                affected_patterns=len(analysis.error_patterns),
            ))
        
        # Low input diversity suggestion
        if analysis.unique_input_patterns < analysis.total_calls * 0.01:
            suggestions.append(RefactoringSuggestion(
                description="Very low input diversity - consider caching or memoization",
                confidence=0.7,
                impact="medium",
                current_signature="",
                suggested_signature=None,
                supporting_calls=[],
                reasoning=f"Only {analysis.unique_input_patterns} unique input patterns in {analysis.total_calls} calls",
                breaking_change=False,
                affected_patterns=analysis.unique_input_patterns,
            ))
        
        return suggestions
    
    def validate_refactoring(
        self,
        function_name: str,
        new_implementation: Callable,
        sample_size: int = 100,
    ) -> ReplayReport:
        """
        Validate a refactored implementation against captured behavior.
        
        Returns a detailed report of any behavioral differences.
        """
        return self._replay_engine.replay(
            function_name=function_name,
            new_implementation=new_implementation,
            limit=sample_size,
        )

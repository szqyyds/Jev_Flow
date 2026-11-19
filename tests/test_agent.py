import unittest
from agent_core import (Agent, Session, OfflineRouter, JevRouter, ProviderError,
                        safe_calculate, ToolError, build_args, validate, DeepSeekResponder)


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(OfflineRouter())

    def test_calculation(self):
        out = self.agent.run("计算 18 + 24")
        self.assertEqual(out["status"], "completed")
        self.assertIn("42", out["answer"])

    def test_no_arbitrary_python_eval(self):
        with self.assertRaises(ToolError):
            safe_calculate("__import__('os').system('echo hi')")

    def test_missing_order_clarify_then_continue(self):
        s = Session()
        one = self.agent.run("帮我查一下订单", s)
        self.assertEqual(one["status"], "request_info")
        two = self.agent.run("A123", s)
        self.assertEqual(two["status"], "completed")
        self.assertIn("A123", two["answer"])
        self.assertEqual(s.last_order_id, "A123")

    def test_multistep(self):
        out = self.agent.run("查订单 A123，如果没发货解释一下发货规则")
        self.assertEqual(out["status"], "completed")
        self.assertEqual([x["tool"] for x in out["observations"]], ["order", "knowledge"])

    def test_weather_does_not_require_date(self):
        out = self.agent.run("北京天气怎么样")
        self.assertEqual(out["status"], "completed")
        self.assertIn("模拟", out["answer"])

    def test_ambiguous_search_clarify(self):
        out = self.agent.run("检索本地文档")
        self.assertEqual(out["status"], "request_info")

    def test_fake_order_id_never_created(self):
        args, sources = build_args("order", "查一下我的订单", Session())
        self.assertEqual(args, {})
        self.assertEqual(validate("order", args, sources), ["order_id"])

    def test_unsupported(self):
        out = self.agent.run("发送一封邮件")
        self.assertEqual(out["status"], "cannot_answer")

    def test_jev_adapter_true_request_shapes(self):
        seen = []
        def fake_wire(payload):
            seen.append(payload)
            return {"answers": {
                "action": {"type":"choice", "choice":"tool_call", "confidence":0.93},
                "tool": {"type":"choice", "choice":"weather", "confidence":0.87}},
                "usage":{"input_tokens":80, "output_tokens":5}}
        router = JevRouter(api_key="", transport=fake_wire)
        out = Agent(router).run("北京天气怎么样")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["route"]["provider"], "jev")
        self.assertEqual(seen[0]["questions"]["action"]["type"], "choice")
        self.assertEqual(out["jev_usage"]["input_tokens"], 80)

    def test_jev_low_confidence_fails_closed(self):
        def fake_wire(payload):
            return {"answers": {
                "action": {"choice":"tool_call", "confidence":0.2},
                "tool": {"choice":"order", "confidence":0.8}}}
        out = Agent(JevRouter("", transport=fake_wire)).run("查订单 A123")
        self.assertEqual(out["status"], "review_needed")
        self.assertFalse(any(e["stage"] == "tool_result" for e in out["events"]))

    def test_jev_missing_field_does_not_invent(self):
        def fake_wire(payload):
            return {"answers": {
                "action": {"choice":"tool_call", "confidence":0.9},
                "tool": {"choice":"order", "confidence":0.9}}}
        out = Agent(JevRouter("", transport=fake_wire)).run("帮我查一下订单")
        self.assertEqual(out["status"], "request_info")
        self.assertFalse(any(e["stage"] == "tool_result" for e in out["events"]))

    def test_optional_deepseek_responder(self):
        seen = []
        def fake_wire(payload):
            seen.append(payload)
            return {"choices": [{"message": {"content": "北京天气晴（模拟数据）"}}], "usage": {"total_tokens": 40}}
        reply = DeepSeekResponder("", transport=fake_wire)
        out = Agent(OfflineRouter(), responder=reply).run("北京天气怎么样")
        self.assertEqual(out["status"], "completed")
        self.assertIn("北京", out["answer"])
        self.assertIn("generator_usage", out)
        self.assertIn("已验证的工具观察", seen[0]["messages"][1]["content"])

    def test_bad_response_is_system_error_not_refuse(self):
        router = JevRouter("", transport=lambda payload: {"unexpected": True})
        out = Agent(router).run("北京天气怎么样")
        self.assertEqual(out["status"], "system_error")
        self.assertNotEqual(out["status"], "cannot_answer")

    def test_jev_clarification_without_tool_resumes_order(self):
        requests = []
        def wire(payload):
            requests.append(payload)
            complete = "A123" in payload["state"]["query"]
            return {"answers": {
                "action": {"choice": "tool_call" if complete else "request_info", "confidence": 0.95},
                "tool": {"choice": "order" if complete else "none", "confidence": 0.95}}}
        agent = Agent(JevRouter("", transport=wire))
        session = Session()
        first = agent.run("查询一下订单", session)
        self.assertEqual(first["status"], "request_info")
        self.assertIn("订单号", first["answer"])
        self.assertFalse(any(e["stage"] == "tool_result" for e in first["events"]))
        second = agent.run("A123", session)
        self.assertEqual(second["status"], "completed")
        self.assertIn("查询一下订单", requests[1]["state"]["query"])
        self.assertEqual(second["observations"][0]["output"]["order_id"], "A123")

    def test_clarification_recovery_does_not_authorize_execution(self):
        for action, confidence, query in [
            ("request_info", 0.95, "查询订单 A123"),
            ("request_info", 0.1, "查询一下订单"),
            ("cannot_answer", 0.95, "查询一下订单"),
            ("request_info", 0.95, "帮我处理一下")]:
            with self.subTest(action=action, confidence=confidence, query=query):
                def wire(payload):
                    return {"answers": {
                        "action": {"choice": action, "confidence": confidence},
                        "tool": {"choice": "none", "confidence": 0.95}}}
                result = Agent(JevRouter("", transport=wire)).run(query)
                self.assertIn(result["status"], ("review_needed", "cannot_answer"))
                self.assertFalse(any(e["stage"] == "tool_result" for e in result["events"]))


if __name__ == "__main__":
    unittest.main()

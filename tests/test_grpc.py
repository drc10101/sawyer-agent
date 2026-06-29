"""Tests for Sawyer gRPC protocol and server."""

from sawyer.config import SawyerConfig
from sawyer.proto import sawyer_pb2
from sawyer.router.server import SawyerNodeServicer, SawyerRouterServicer

# ── Proto message tests ──


class TestProtoMessages:
    """Verify proto messages can be created and serialized."""

    def test_register_node_request(self):
        req = sawyer_pb2.RegisterNodeRequest(
            node_name="test-node",
            capabilities=sawyer_pb2.NodeCapabilities(
                gpu_name="RTX 3090",
                vram_bytes=24 * 1024**3,
                max_experts=2,
                bandwidth_mbps=100.0,
                region="us-east-1",
                supported_models=["mixtral-8x7b"],
            ),
        )
        assert req.node_name == "test-node"
        assert req.capabilities.gpu_name == "RTX 3090"
        assert req.capabilities.vram_bytes == 24 * 1024**3
        assert "mixtral-8x7b" in req.capabilities.supported_models

    def test_register_node_response(self):
        resp = sawyer_pb2.RegisterNodeResponse(
            node_id="sawyer-node-1",
            session_token="st-abc-123",
        )
        assert resp.node_id == "sawyer-node-1"
        assert resp.session_token == "st-abc-123"

    def test_infer_request(self):
        import struct

        tokens = struct.pack("4i", 1, 2, 3, 4)
        req = sawyer_pb2.InferRequest(
            model_name="mixtral-8x7b",
            input_tokens=tokens,
            max_output_tokens=100,
            user_id="user-1",
            api_key="sawyer-key-1",
            params=sawyer_pb2.InferParams(
                temperature=0.7,
                top_p=0.9,
                top_k=50,
            ),
        )
        assert req.model_name == "mixtral-8x7b"
        assert req.max_output_tokens == 100
        assert req.params.temperature == 0.7

    def test_infer_response(self):
        resp = sawyer_pb2.InferResponse(
            output_text="Hello world",
            metrics=sawyer_pb2.InferMetrics(
                input_tokens=4,
                output_tokens=10,
                latency_ms=50.0,
                routing=[
                    sawyer_pb2.ExpertRouting(
                        model_name="mixtral-8x7b",
                        expert_id=0,
                        node_id="node-a",
                        expert_latency_ms=25.0,
                    ),
                ],
            ),
            tokens_remaining=499990,
        )
        assert resp.output_text == "Hello world"
        assert resp.metrics.input_tokens == 4
        assert len(resp.metrics.routing) == 1

    def test_load_expert_request(self):
        req = sawyer_pb2.LoadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=3,
            weight_url="https://huggingface.co/TheBloke/Mixtral-8x7B-v0.1-GGUF/resolve/main/mixtral-8x7b-v0.1.Q4_K_M-exp03.gguf",
            weight_checksum="abc123",
        )
        assert req.model_name == "mixtral-8x7b"
        assert req.expert_id == 3

    def test_expert_infer_request(self):
        req = sawyer_pb2.ExpertInferRequest(
            model_name="mixtral-8x7b",
            expert_id=0,
            hidden_states=b"\x00" * 1024,
            hidden_shape=sawyer_pb2.TensorShape(dims=[1, 4, 4096], dtype="float16"),
            layer_idx=0,
        )
        assert req.model_name == "mixtral-8x7b"
        assert len(req.hidden_states) == 1024
        assert req.hidden_shape.dtype == "float16"

    def test_list_models_response(self):
        from sawyer.model.registry import list_models

        models = list_models()
        model_infos = [
            sawyer_pb2.ModelInfo(
                name=m.name,
                display_name=m.display_name,
                total_params=int(m.total_params_b * 1e9),
                num_experts=m.num_experts,
                active_experts=m.active_experts,
            )
            for m in models
        ]
        assert len(model_infos) >= 4
        assert model_infos[0].name == "mixtral-8x7b"

    def test_heartbeat_request(self):
        req = sawyer_pb2.HeartbeatRequest(
            node_id="sawyer-node-1",
            status=sawyer_pb2.NodeStatus(
                cpu_usage=45.0,
                gpu_usage=80.0,
                vram_used_bytes=12 * 1024**3,
                vram_total_bytes=24 * 1024**3,
                active_requests=3,
                total_inferences=1500,
                avg_latency_ms=25.0,
            ),
        )
        assert req.node_id == "sawyer-node-1"
        assert req.status.gpu_usage == 80.0

    def test_health_check_response(self):
        resp = sawyer_pb2.HealthCheckResponse(
            healthy=True,
            loaded_experts=[
                sawyer_pb2.LoadedExpert(
                    model_name="mixtral-8x7b",
                    expert_id=0,
                    vram_bytes=int(1.5 * 1024**3),
                    inference_count=500,
                ),
            ],
            status=sawyer_pb2.NodeStatus(
                vram_used_bytes=int(1.5 * 1024**3),
                vram_total_bytes=24 * 1024**3,
            ),
        )
        assert resp.healthy is True
        assert len(resp.loaded_experts) == 1


# ── Router servicer unit tests ──


class TestRouterServicer:
    """Test SawyerRouterServicer without gRPC transport."""

    def setup_method(self):
        self.config = SawyerConfig()
        self.servicer = SawyerRouterServicer(self.config)

    def test_register_node(self):
        request = sawyer_pb2.RegisterNodeRequest(
            node_name="test-node",
            capabilities=sawyer_pb2.NodeCapabilities(
                gpu_name="RTX 3090",
                vram_bytes=24 * 1024**3,
                max_experts=2,
                bandwidth_mbps=100.0,
                region="us-east-1",
                supported_models=["mixtral-8x7b"],
            ),
        )
        response = self.servicer.RegisterNode(request, None)
        assert response.node_id.startswith("sawyer-node-")
        assert response.session_token.startswith("st-")
        assert len(response.assignments) == 1

    def test_heartbeat(self):
        # Register first
        reg_req = sawyer_pb2.RegisterNodeRequest(
            node_name="test-node",
            capabilities=sawyer_pb2.NodeCapabilities(gpu_name="RTX 3090"),
        )
        reg_resp = self.servicer.RegisterNode(reg_req, None)

        # Send heartbeat
        hb_req = sawyer_pb2.HeartbeatRequest(
            node_id=reg_resp.node_id,
            status=sawyer_pb2.NodeStatus(avg_latency_ms=15.0, total_inferences=100),
        )
        hb_resp = self.servicer.Heartbeat(hb_req, None)
        assert hb_resp.acknowledged is True

    def test_list_models(self):
        request = sawyer_pb2.ListModelsRequest()
        response = self.servicer.ListModels(request, None)
        assert len(response.models) >= 4
        names = [m.name for m in response.models]
        assert "mixtral-8x7b" in names
        assert "deepseek-v2-lite" in names


# ── Node servicer unit tests ──


class TestNodeServicer:
    """Test SawyerNodeServicer without gRPC transport."""

    def setup_method(self):
        self.config = SawyerConfig(max_vram_gb=24.0)
        self.servicer = SawyerNodeServicer(self.config)

    def test_load_expert(self):
        request = sawyer_pb2.LoadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=0,
        )
        response = self.servicer.LoadExpert(request, None)
        assert response.success is True
        assert response.vram_used_bytes > 0

    def test_load_expert_idempotent(self):
        request = sawyer_pb2.LoadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=0,
        )
        resp1 = self.servicer.LoadExpert(request, None)
        resp2 = self.servicer.LoadExpert(request, None)
        assert resp1.success is True
        assert resp2.success is True
        assert resp1.vram_used_bytes == resp2.vram_used_bytes

    def test_unload_expert(self):
        # Load first
        load_req = sawyer_pb2.LoadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=0,
        )
        self.servicer.LoadExpert(load_req, None)

        # Unload
        unload_req = sawyer_pb2.UnloadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=0,
        )
        response = self.servicer.UnloadExpert(unload_req, None)
        assert response.success is True
        assert response.vram_freed_bytes > 0

    def test_unload_nonexistent_expert(self):
        request = sawyer_pb2.UnloadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=99,
        )
        response = self.servicer.UnloadExpert(request, None)
        assert response.success is False
        assert "not loaded" in response.error_message

    def test_health_check_empty(self):
        request = sawyer_pb2.HealthCheckRequest(node_id="test-node")
        response = self.servicer.HealthCheck(request, None)
        assert response.healthy is True
        assert len(response.loaded_experts) == 0

    def test_health_check_with_experts(self):
        # Load an expert first
        load_req = sawyer_pb2.LoadExpertRequest(
            model_name="mixtral-8x7b",
            expert_id=0,
        )
        self.servicer.LoadExpert(load_req, None)

        request = sawyer_pb2.HealthCheckRequest(node_id="test-node")
        response = self.servicer.HealthCheck(request, None)
        assert response.healthy is True
        assert len(response.loaded_experts) == 1
        assert response.loaded_experts[0].model_name == "mixtral-8x7b"

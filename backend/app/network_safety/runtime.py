from app.network_safety.controller import NetworkExecutionController
from app.network_safety.gateway import NetworkGateway


network_execution_controller = NetworkExecutionController()
network_gateway = NetworkGateway(controller=network_execution_controller)

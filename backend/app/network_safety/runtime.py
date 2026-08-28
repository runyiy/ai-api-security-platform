from app.db.session import network_coordination_engine
from app.network_safety.gateway import NetworkGateway
from app.network_safety.postgres_controller import PostgresNetworkExecutionController


network_execution_controller = PostgresNetworkExecutionController(
    bind=network_coordination_engine
)
network_gateway = NetworkGateway(controller=network_execution_controller)

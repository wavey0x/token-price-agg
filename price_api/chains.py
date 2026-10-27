"""The supported networks and their provider-specific names."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    name: str
    native_symbol: str
    defillama_name: str
    curve_name: str

    @property
    def rpc_env(self) -> str:
        return f"{self.name.upper()}_RPC_URL"


CHAINS = {
    1: Chain("ethereum", "ETH", "ethereum", "ethereum"),
    10: Chain("optimism", "ETH", "optimism", "optimism"),
    100: Chain("gnosis", "XDAI", "xdai", "xdai"),
    137: Chain("polygon", "POL", "polygon", "polygon"),
    42161: Chain("arbitrum", "ETH", "arbitrum", "arbitrum"),
    43114: Chain("avalanche", "AVAX", "avax", "avalanche"),
}

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

MULTICALL3_BY_CHAIN = dict.fromkeys(CHAINS, MULTICALL3_ADDRESS)

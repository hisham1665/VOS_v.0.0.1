"""Medical AOS extension.

A Doctor-Assistant workflow layered on top of the existing AOS kernel. It adds
medical capabilities, agents and model integrations while leaving the Capability
DNA, orchestration, agent selection, fault-recovery and routing mechanisms
unchanged -- every medical capability is registered as an ordinary
CapabilityManifest in the existing registry and is routed/selected/recovered by
the existing machinery.
"""

__all__ = [
    "workflow",
    "registration",
    "capabilities",
    "manifest",
    "clinical",
    "hf_medical",
]

__version__ = "0.1.0"
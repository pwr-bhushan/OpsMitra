# ADR-0001: Use Deterministic Detection With Local Model Summaries

**Date**: 2026-05-28
**Status**: accepted
**Deciders**: Bhushan, Codex

## Context

OpsMitra is a learning project for AWS services, local/private model integration, and AI-native backend design. The motivating incident class is operational abuse or runaway usage, such as an SMS API sending thousands of messages in a short period. These incidents need reliable detection, low noise, and evidence-rich alerts.

## Decision

OpsMitra will use deterministic detectors for anomaly detection and a local/private model only for summarizing structured anomaly evidence into human-readable alerts.

## Alternatives Considered

### Model-First Detection

- **Pros**: Simpler demo story and more visibly AI-native.
- **Cons**: Hard to test, hard to calibrate, vulnerable to false positives and inconsistent reasoning.
- **Why not**: The project should teach reliable AI engineering, not make the model responsible for simple math and thresholds.

### External Hosted LLM Summaries

- **Pros**: Easier setup and likely stronger language quality.
- **Cons**: Sends operational log context outside the local/private boundary.
- **Why not**: Privacy is one of the core learning constraints for this project.

### AWS-Native Alerts Only

- **Pros**: Reliable, cheap, and close to production AWS practice.
- **Cons**: Does not teach local model integration or AI summarization patterns.
- **Why not**: OpsMitra is meant to combine backend/AWS learning with private AI app design.

## Consequences

### Positive

- Detectors are testable and deterministic.
- Alerts can include model-generated explanation without trusting the model for detection.
- Local tests can validate detector quality without a running model.

### Negative

- More code is required than a model-only prototype.
- Detector thresholds and baselines must be designed explicitly.
- The AI layer may feel less magical, but it will be more useful.

### Risks

- Poor detector thresholds can still create noisy alerts; mitigate with replay evaluation.
- Local model setup can be operationally annoying; mitigate with a deterministic fallback summarizer.

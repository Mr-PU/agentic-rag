#!/usr/bin/env bash
# Builds the api/worker image and deploys the full stack to a local Kubernetes
# cluster (kind or minikube). Assumes kubectl is already pointed at that cluster.
#
# Usage: ./scripts/k8s_deploy.sh [kind|minikube]

set -euo pipefail

CLUSTER_TYPE="${1:-kind}"
IMAGE_TAG="agentic-rag-api:local"

echo "== Building image ($IMAGE_TAG) =="
docker build -t "$IMAGE_TAG" ./backend

echo
echo "== Loading image into the $CLUSTER_TYPE cluster =="
case "$CLUSTER_TYPE" in
  kind)
    kind load docker-image "$IMAGE_TAG"
    ;;
  minikube)
    minikube image load "$IMAGE_TAG"
    ;;
  *)
    echo "Unknown cluster type '$CLUSTER_TYPE'. Use 'kind' or 'minikube', or load the image manually."
    exit 1
    ;;
esac

echo
echo "== Applying base manifests (namespace, data infra, api, worker) =="
kubectl apply -k k8s/overlays/local

echo
echo "== Waiting for ollama to become available (this can take a minute) =="
kubectl wait --for=condition=available deployment/ollama -n agentic-rag --timeout=300s

echo
echo "== Pulling models into ollama (this can take several minutes on first run) =="
kubectl delete job ollama-pull -n agentic-rag --ignore-not-found
kubectl apply -f k8s/base/ollama-pull-job.yaml
kubectl wait --for=condition=complete job/ollama-pull -n agentic-rag --timeout=1800s

echo
echo "== Waiting for api to become available =="
kubectl wait --for=condition=available deployment/api -n agentic-rag --timeout=300s

echo
echo "Done. Access the app with:"
echo "  kubectl port-forward svc/api 8000:8000 -n agentic-rag"
echo "then open http://localhost:8000"

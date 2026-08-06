using UnityEngine;

namespace SwarmMarl
{
    public sealed class SwarmTrainingArea : MonoBehaviour
    {
        [SerializeField] private SwarmDroneAgent[] agents;
        [SerializeField] private Transform[] targets;
        [SerializeField] private Vector3 boundsMin = new Vector3(-5.0f, -5.0f, 0.0f);
        [SerializeField] private Vector3 boundsMax = new Vector3(5.0f, 5.0f, 5.0f);
        [SerializeField] private float safeDistance = 1.6f;
        [SerializeField] private float collisionDistance = 0.25f;
        [SerializeField] private float successDistance = 0.2f;
        [SerializeField] private float progressReward = 1.0f;
        [SerializeField] private float successReward = 5.0f;
        [SerializeField] private float nearPenalty = 1.0f;
        [SerializeField] private float collisionPenalty = 10.0f;
        [SerializeField] private float boundaryPenalty = 5.0f;
        [SerializeField] private float actionSmoothPenalty = 0.05f;

        public SwarmDroneAgent[] Agents => agents;
        public Transform[] Targets => targets;
        public float SafeDistance => safeDistance;
        public float CollisionDistance => collisionDistance;
        public float SuccessDistance => successDistance;
        public float ProgressReward => progressReward;
        public float SuccessReward => successReward;
        public float NearPenalty => nearPenalty;
        public float CollisionPenalty => collisionPenalty;
        public float BoundaryPenalty => boundaryPenalty;
        public float ActionSmoothPenalty => actionSmoothPenalty;
        public float ObservationPositionScale => Mathf.Max(1.0f, (boundsMax - boundsMin).magnitude);

        public bool IsOutOfBounds(Vector3 position)
        {
            return position.x < boundsMin.x ||
                position.y < boundsMin.y ||
                position.z < boundsMin.z ||
                position.x > boundsMax.x ||
                position.y > boundsMax.y ||
                position.z > boundsMax.z;
        }

        public Vector3 ClampToBounds(Vector3 position)
        {
            return new Vector3(
                Mathf.Clamp(position.x, boundsMin.x, boundsMax.x),
                Mathf.Clamp(position.y, boundsMin.y, boundsMax.y),
                Mathf.Clamp(position.z, boundsMin.z, boundsMax.z));
        }

        public float SeparationPenalty(SwarmDroneAgent self)
        {
            if (agents == null)
            {
                return 0.0f;
            }

            float penalty = 0.0f;
            foreach (SwarmDroneAgent agent in agents)
            {
                if (agent == null || agent == self)
                {
                    continue;
                }

                float separation = Vector3.Distance(self.transform.position, agent.transform.position);
                if (separation < collisionDistance)
                {
                    penalty -= collisionPenalty;
                }
                else if (separation < safeDistance)
                {
                    penalty -= nearPenalty * (safeDistance - separation) / safeDistance;
                }
            }

            return penalty;
        }

        public bool HasCollision(SwarmDroneAgent self)
        {
            if (agents == null)
            {
                return false;
            }

            foreach (SwarmDroneAgent agent in agents)
            {
                if (agent == null || agent == self)
                {
                    continue;
                }

                if (Vector3.Distance(self.transform.position, agent.transform.position) < collisionDistance)
                {
                    return true;
                }
            }

            return false;
        }

        public void ResetAgents()
        {
            if (agents == null)
            {
                return;
            }

            for (int i = 0; i < agents.Length; i += 1)
            {
                if (agents[i] == null)
                {
                    continue;
                }

                Transform target = targets != null && i < targets.Length ? targets[i] : null;
                agents[i].ResetTrainingState(RandomPoint(), target);
            }
        }

        public Vector3 RandomPoint()
        {
            return new Vector3(
                Random.Range(boundsMin.x, boundsMax.x),
                Random.Range(boundsMin.y, boundsMax.y),
                Random.Range(boundsMin.z, boundsMax.z));
        }
    }
}

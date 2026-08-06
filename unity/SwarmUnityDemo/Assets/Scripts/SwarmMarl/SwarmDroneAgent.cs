using UnityEngine;

#if SWARM_ML_AGENTS
using Unity.MLAgents;
using Unity.MLAgents.Actuators;
using Unity.MLAgents.Sensors;
#endif

namespace SwarmMarl
{
#if SWARM_ML_AGENTS
    public sealed class SwarmDroneAgent : Agent
#else
    public sealed class SwarmDroneAgent : MonoBehaviour
#endif
    {
        [SerializeField] private SwarmTrainingArea area;
        [SerializeField] private Transform target;
        [SerializeField] private int maxNeighbors = 3;
        [SerializeField] private float maxSpeed = 1.0f;
        [SerializeField] private float maxAcceleration = 2.0f;

        private Vector3 velocity;
        private Vector3 previousAction;
        private float previousTargetDistance;

        public Vector3 Velocity => velocity;
        public Transform Target => target;

        public void ResetTrainingState(Vector3 position, Transform assignedTarget)
        {
            transform.position = position;
            target = assignedTarget;
            velocity = Vector3.zero;
            previousAction = Vector3.zero;
            previousTargetDistance = CurrentTargetDistance();
        }

#if SWARM_ML_AGENTS
        public override void Initialize()
        {
            previousTargetDistance = CurrentTargetDistance();
        }

        public override void OnEpisodeBegin()
        {
            if (area != null)
            {
                area.ResetAgents();
            }
        }

        public override void CollectObservations(VectorSensor sensor)
        {
            float positionScale = area != null ? area.ObservationPositionScale : 10.0f;
            sensor.AddObservation(transform.position / positionScale);
            sensor.AddObservation(velocity / Mathf.Max(0.001f, maxSpeed));

            Vector3 targetDelta = target != null ? target.position - transform.position : Vector3.zero;
            sensor.AddObservation(targetDelta / positionScale);

            int neighborCount = 0;
            if (area != null && area.Agents != null)
            {
                foreach (SwarmDroneAgent agent in area.Agents)
                {
                    if (agent == null || agent == this)
                    {
                        continue;
                    }

                    if (neighborCount >= maxNeighbors)
                    {
                        break;
                    }

                    sensor.AddObservation((agent.transform.position - transform.position) / positionScale);
                    sensor.AddObservation((agent.Velocity - velocity) / Mathf.Max(0.001f, maxSpeed));
                    neighborCount += 1;
                }
            }

            while (neighborCount < maxNeighbors)
            {
                sensor.AddObservation(Vector3.zero);
                sensor.AddObservation(Vector3.zero);
                neighborCount += 1;
            }
        }

        public override void OnActionReceived(ActionBuffers actions)
        {
            Vector3 normalizedAction = Vector3.zero;
            if (actions.ContinuousActions.Length >= 3)
            {
                normalizedAction = new Vector3(
                    Mathf.Clamp(actions.ContinuousActions[0], -1.0f, 1.0f),
                    Mathf.Clamp(actions.ContinuousActions[1], -1.0f, 1.0f),
                    Mathf.Clamp(actions.ContinuousActions[2], -1.0f, 1.0f));
            }

            ApplyAction(normalizedAction);
            AddStepReward(normalizedAction);
            previousAction = normalizedAction;
        }

        public override void Heuristic(in ActionBuffers actionsOut)
        {
            ActionSegment<float> continuous = actionsOut.ContinuousActions;
            continuous[0] = 0.0f;
            continuous[1] = 0.0f;
            continuous[2] = 0.0f;
        }
#endif

        private void ApplyAction(Vector3 normalizedAction)
        {
            Vector3 desiredVelocity = Vector3.ClampMagnitude(normalizedAction, 1.0f) * maxSpeed;
            velocity = Vector3.MoveTowards(velocity, desiredVelocity, maxAcceleration * Time.fixedDeltaTime);
            Vector3 nextPosition = transform.position + velocity * Time.fixedDeltaTime;
            transform.position = nextPosition;
        }

#if SWARM_ML_AGENTS
        private void AddStepReward(Vector3 normalizedAction)
        {
            float currentDistance = CurrentTargetDistance();
            float progress = previousTargetDistance - currentDistance;
            AddReward(progress * (area != null ? area.ProgressReward : 1.0f));
            previousTargetDistance = currentDistance;

            if (area != null)
            {
                AddReward(area.SeparationPenalty(this));

                float smoothnessPenalty = area.ActionSmoothPenalty * (normalizedAction - previousAction).sqrMagnitude;
                AddReward(-smoothnessPenalty);

                if (currentDistance <= area.SuccessDistance)
                {
                    AddReward(area.SuccessReward);
                    EndEpisode();
                    return;
                }

                if (area.HasCollision(this))
                {
                    AddReward(-area.CollisionPenalty);
                    EndEpisode();
                    return;
                }

                if (area.IsOutOfBounds(transform.position))
                {
                    AddReward(-area.BoundaryPenalty);
                    EndEpisode();
                }
            }
        }
#endif

        private float CurrentTargetDistance()
        {
            return target != null ? Vector3.Distance(transform.position, target.position) : 0.0f;
        }
    }
}

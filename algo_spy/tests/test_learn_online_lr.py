import random

from algo_spy.learn.online_lr import OnlineLogisticRegression


def test_learns_linearly_separable():
    rng = random.Random(0)
    model = OnlineLogisticRegression(n_features=2, lr=0.1, l2=1e-4)
    correct = 0
    total = 0
    for i in range(4000):
        x0 = rng.uniform(-1, 1)
        x1 = rng.uniform(-1, 1)
        y = 1 if (2.0 * x0 - 1.0 * x1) > 0 else 0
        p = model.predict_proba([x0, x1])  # predict BEFORE update (prequential)
        if i > 2000:
            total += 1
            correct += int((p >= 0.5) == bool(y))
        model.update([x0, x1], y)
    assert correct / total > 0.9

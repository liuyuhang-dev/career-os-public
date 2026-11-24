// testflow 扫描器 fixture —— 故意包含防护缺失,用于验证扫描器能报出来。
// 这不是可编译的完整项目,只是给 scripts/scan_idempotency.py 吃的样本。

using System;

namespace Game.Shop
{
    public class ShopManager
    {
        // 缺失:状态守卫、回调去重、重放去重。
        // 有幂等键参数 orderId,但函数体内没有任何状态判断,也没有"这笔处理过没"的查询。
        public void BuyItem(string itemId, string orderId)
        {
            var item = Config.GetItem(itemId);
            Pay(item.Price, orderId);
            DeliverItem(itemId, 1);
            UpdatePlayerCoin(-item.Price);
        }

        // 缺失:四类防护一个都没有。
        // 外部回调重投时会重复发货 —— 这是重复支付最典型的来源。
        public void OnPayCallback(string payloadJson)
        {
            var cb = Json.Parse(payloadJson);
            DeliverItem(cb.ItemId, 1);
            UpdatePlayerCoin(-cb.Price);
            Save();
        }

        // 缺失:重放去重、幂等键。
        // 有状态守卫(if status == "Unclaimed"),所以重复领取被挡住了 —— 这是防护的第二道。
        public void ClaimMailReward(string mailId)
        {
            if (status == "Unclaimed")
            {
                status = "Claimed";
                GrantReward(mailId);
            }
        }

        // 四类防护齐备,应当报"无缺失"。作为对照组,验证扫描器不是无脑报警。
        public void SettleBattle(string battleId, string requestId)
        {
            if (state == "Playing")
            {
                if (dedup.Exists(requestId)) return;
                state = "Settled";
                AddCoin(calculatedReward);
                appliedOps.Add(requestId);
                Save();
            }
        }

        // 名字匹配但没有副作用调用,默认(--only-side-effect)应当被过滤掉。
        public void ResetOrderCache()
        {
            cache.Clear();
        }
    }
}

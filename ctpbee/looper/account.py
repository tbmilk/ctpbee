"""
* 账户模块, 存储资金修改, 负责对外部的成交单进行成交撮合 并扣除手续费 等操作
* 需要向外提供API
    trading: 发起交易
    is_traded: 是否可以进行交易
    result: 回测结果
"""

from collections import defaultdict

import numpy as np
from pandas import DataFrame

from ctpbee.constant import TradeData, OrderData, Offset, PositionData, Direction, AccountData
from ctpbee.exceptions import ConfigError
from ctpbee.looper.local_position import LocalPositionManager
import uuid


class AliasDayResult:
    """
    每天的结果
    """

    def __init__(self, **kwargs):
        """ 实例化进行调用 """
        for i, v in kwargs.items():
            setattr(self, i, v)

    def __repr__(self):
        result = "DailyResult: { "
        for x in dir(self):
            if x.startswith("_"):
                continue
            result += f"{x}:{getattr(self, x)} "
        return result + "}"

    def _to_dict(self):
        return self.__dict__


class Account:
    """
    账户类

    支持成交之后修改资金 ， 对外提供API

    """

    def __init__(self, interface, name=None):
        self.account_id = name if name is not None else uuid.uuid4()
        # 成交接口
        self.interface = interface
        # 每日成交单信息
        self.daily_life = defaultdict(AliasDayResult)

        # 合约乘数
        self.sizemap = {}
        # 每跳价格变化
        self.pricetick = 10
        # 每日下单限制
        self.daily_limit = 20

        # 账户当前的日期
        self.date = None
        self.count_statistics = 0
        # 初始资金
        self.initial_capital = 0
        # 账户权益
        self.balance = 100000
        self.long_margin = 0
        self.short_margin = 0
        self.long_frozen_margin = 0
        self.short_frozen_margin = 0
        self.frozen_fee = 0
        self.frozen_premium = 0
        """ 
        fee应该是一个
        {
        ag2012.SHFE: 200.1
        }的是字典"""
        self.fee = {

        }
        self.init_position_manager_flag = False
        self.init = False
        self.position_manager = None
        self.margin_ratio = {}
        # commission_ratio 应该为{"ag2012.SHFE": {"close_today": 0.005, "close":0.005 }
        self.commission_ratio = defaultdict(dict)

    @property
    def margin(self):
        return self.long_margin + self.short_margin

    @property
    def frozen_margin(self):
        return self.long_frozen_margin + self.short_frozen_margin

    @property
    def to_object(self) -> AccountData:
        return AccountData._create_class(dict(accountid=self.account_id,
                                              local_account_id=f"{self.account_id}.SIM",
                                              frozen=self.frozen,
                                              balance=self.balance,
                                              ))

    def release_margin(self, volume, direction, local_symbol):
        """ 平仓需要释放的保证金 """
        pos = self.position_manager.get_position_by_ld(local_symbol=local_symbol, direction=direction)
        if pos:
            return pos.price * volume * self.sizemap.get(local_symbol) * self.margin_ratio.get(local_symbol)
        else:
            print("无此仓位")

    @property
    def available(self) -> float:
        return self.balance - self.margin - self.frozen_margin - self.frozen_fee - self.frozen_premium

    def update_attr(self, data: TradeData or OrderData):
        """ 更新基础属性方法
        # 下单更新冻结的保证金
        # 成交更新持仓的保证金
        开仓手续费 /平仓手续费 平今手续费
        """
        if isinstance(data, TradeData):
            """ 成交属性 """
            try:
                if data.offset == Offset.CLOSETODAY:
                    ratio = self.commission_ratio.get(data.local_symbol)["close_today"]
                elif data.offset == Offset.OPEN:
                    ratio = self.commission_ratio.get(data.local_symbol)["open"]
                else:
                    ratio = self.commission_ratio.get(data.local_symbol)["close"]
            except KeyError:
                raise ValueError("请在对应品种设置合理的手续费")
            if self.fee.get(data.local_symbol) is None:
                self.fee[data.local_symbol] = data.price * data.volume * ratio
            else:
                self.fee[data.local_symbol] += data.price * data.volume * ratio

            if data.offset == Offset.OPEN:
                """  开仓增加保证金 """
                if data.direction == Direction.LONG:
                    self.long_margin += self.margin_ratio.get(
                        data.local_symbol) * data.price * data.volume * self.sizemap.get(data.local_symbol)
                else:
                    self.short_margin += self.margin_ratio.get(
                        data.local_symbol) * data.price * data.volume * self.sizemap.get(data.local_symbol)

                self.balance -= data.price * data.volume
            else:
                """ todo: 平仓移除保证金 """
                if data.direction == Direction.LONG:
                    release_margin_amount = self.release_margin(data.volume, Direction.SHORT, data.local_symbol)
                    self.short_margin += release_margin_amount
                else:
                    release_margin_amount = self.release_margin(data.volume, Direction.LONG, data.local_symbol)
                    self.long_margin += release_margin_amount
                self.balance += data.price * data.volume

        if isinstance(data, OrderData):
            """ 发单属性 todo: 发单增加冻结 撤单时候归还冻结  """
            if data.offset == Offset.OPEN:
                if data.direction == Direction.LONG:
                    pass
                else:
                    pass
            else:
                if data.direction == Direction.LONG:
                    pass
                else:
                    pass

    def reset_attr(self):
        self.long_margin = 0
        self.short_margin = 0
        self.long_frozen_margin = 0
        self.short_frozen_margin = 0
        self.frozen_fee = 0
        self.frozen_premium = 0

    def is_traded(self, order: OrderData) -> bool:
        """ 当前账户是否足以支撑成交 """
        # 根据传入的单子判断当前的账户可用资金是否足以成交此单
        if order.price * order.volume * (1 + self.commission) < self.available:
            """ 可用不足"""
            return False
        return True

    def update_trade(self, trade: TradeData) -> None:
        """
        当前选择调用这个接口的时候就已经确保了这个单子是可以成交的，
        make sure it can be traded if you choose to call this method,
        :param trade:交易单子/trade
        :return:
        """
        self.update_attr(trade)
        self.position_manager.update_trade(trade=trade)

    def settle(self, interface_date=None):
        """ 生成今天的交易数据， 同时更新前日数据 ，然后进行持仓结算 """
        if not self.date:
            date = interface_date
        else:
            date = self.date
        """ 结算撤掉所有单 归还冻结 """

        self.interface.pending.clear()
        p = AliasDayResult(
            **{"balance": self.balance + self.occupation_margin,
               "frozen": self.frozen,
               "available": self.balance - self.frozen,
               "date": date, "commission": self.commission_expense - self.pre_commission_expense,
               "net_pnl": self.balance - self.pre_balance,
               "count": self.count_statistics - self.pre_count
               })

        self.pre_commission_expense = self.commission_expense
        self.pre_balance = self.balance
        self.commission = 0
        self.interface.today_volume = 0
        self.position_manager.covert_to_yesterday_holding()
        self.daily_life[date] = p._to_dict()
        # 归还所有的冻结
        self.balance += self.frozen
        self.frozen = 0
        self.date = interface_date

    def via_aisle(self):
        self.position_manager.update_size_map(self.interface.params)
        if self.interface.date != self.date:
            self.settle(self.interface.date)
            self.date = self.interface.date
        else:
            pass

    def update_params(self, params: dict):
        """ 更新本地账户回测参数 """
        for i, v in params.items():
            if i == "initial_capital" and not self.init:
                self.balance = v
                self.pre_balance = v
                self.initial_capital = v
                self.init = True
                continue
            else:
                pass
            setattr(self, i, v)
        if not self.init_position_manager_flag:
            self.position_manager = LocalPositionManager(params)
            self.init_position_manager_flag = True
        else:
            pass

    @property
    def result(self):
        # 根据daily_life里面的数据 获取最后的结果
        result = defaultdict(list)
        for daily in self.daily_life.values():
            for key, value in daily.items():
                result[key].append(value)

        df = DataFrame.from_dict(result).set_index("date")
        try:
            import matplotlib.pyplot as plt
            df['balance'].plot()
            plt.show()

        except ImportError as e:
            pass
        finally:
            return self._cal_result(df)

    def get_mapping(self, d):
        mapping = {}
        for i, v in self.daily_life.items():
            mapping[str(i)] = v.get(d)
        return mapping

    def _cal_result(self, df: DataFrame) -> dict:
        result = dict()
        df["return"] = np.log(df["balance"] / df["balance"].shift(1)).fillna(0)
        df["high_level"] = (
            df["balance"].rolling(
                min_periods=1, window=len(df), center=False).max()
        )
        df["draw_down"] = df["balance"] - df["high_level"]
        df["dd_percent"] = df["draw_down"] / df["high_level"] * 100
        result['initial_capital'] = self.initial_capital
        result['start_date'] = df.index[0]
        result['end_date'] = df.index[-1]
        result['total_days'] = len(df)
        result['profit_days'] = len(df[df["net_pnl"] > 0])
        result['loss_days'] = len(df[df["net_pnl"] < 0])
        result['end_balance'] = df["balance"].iloc[-1]
        result['max_draw_down'] = df["draw_down"].min()
        result['max_dd_percent'] = df["dd_percent"].min()
        result['total_pnl'] = df["net_pnl"].sum()
        result['daily_pnl'] = result['total_pnl'] / result['total_days']
        result['total_commission'] = df["commission"].sum()
        result['daily_commission'] = result['total_commission'] / result['total_days']
        # result['total_slippage'] = df["slippage"].sum()
        # result['daily_slippage'] = result['total_slippage'] / result['total_days']
        # result['total_turnover'] = df["turnover"].sum()
        # result['daily_turnover'] = result['total_turnover'] / result['total_days']
        result['total_count'] = df["count"].sum()
        result['daily_count'] = result['total_count'] / result['total_days']
        result['total_return'] = (result['end_balance'] / self.initial_capital - 1) * 100
        result['annual_return'] = result['total_return'] / result['total_days'] * 240
        result['daily_return'] = df["return"].mean() * 100
        result['return_std'] = df["return"].std() * 100
        return result

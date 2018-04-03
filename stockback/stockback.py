# -*- coding: utf-8 -*-
"""
Created on Fri Aug 11 14:25:12 2017

@author: dylan
"""

import pandas as pd
import numpy as np
import pymysql
import datetime
import time

from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

'''
修改：本次修改了每年超额收益的bug；
备注：可以设定最大的买入股票只数，但是每一期的买入，都是全额投入，比如第一期买入4只股票，
而实际上最大仓位是7只，那么第一期同样投入100%资金，下一期若7只满足条件，则再重新分配资金。

此回溯程序是先挑选出每一期符合条件的股票，然后再开始交易买入
每一期股票buylist: 包含SecuCode,SecuAbbr,signal_rank,三个字段,index是日期格式，若是weight_type!=None,则需要有
weight字段，其中，signal_rank为每一期股票的买入优先级,从小到大，rank越小，优先级越高

quoe:行情数据，至少包含cp,precp,vol,fq_cp[计算止损止盈用]字段，
1. 止盈、止损每日进行判断
'''
 
class trade:
    datapath = "C:\\py_data\\datacenter\\quote.h5"
    unit = 100
    feeratio = 0.0015
    init_cash = 100000000
    volratio = 0.1
    tradeprice = 'cp'
    daily_limit = 0.1 #涨跌停为10%
    min_wait_buycash = 20000
    buynumber = 4
    min_holddays = 30
    stop_type = 'abs'
    loss_rtn = -0.2
    gain_rtn=None
    
        
    def __init__(self,datapath,unit,feeratio,init_cash,volratio,tradeprice,daily_limit,min_wait_buycash,buynumber,min_holddays,stop_type,weight_type=None,loss_rtn=None,gain_rtn=None):
        '''
        
        unit:单位，默认100
        feeratio:手续费，默认千分之1.5
        init_cash:初始投入金额 单位元
        volratio:每日最大成交比例，如10%，对于资金大，单只股票不超过该股票当日成交量的10%
        tradeprice:交易价格
        fq_cp: 复权交易价格，计算止盈止损用
        daily_limit:涨跌停幅度，如0.095，超过该涨幅，当日股票无法交易
        min_wait_buycash:最小保留金额，小于这个金额不再新开仓买入，默认20000 
        buynumber：最大持仓数量
        min_holddays：单只股票最小持仓数量
        stop_type: 'relative':相对模式，如个股票盈利10%，而基准盈利-10%，那么相对盈利20%，止盈止损位相对，其他为绝对止损........
        weight_type: 每期权重方式，None默认等权重,否则需要buylist有"weight"字段
        loss_rtn: 止损收益率，如-0.1，代表个股亏损10%止损
        gain_rtn: 止盈收益率，如0.2，代表个股盈利20%止盈
        -------------------------------------------------------------------------
        一旦设定止盈止损，则即使最新一期调入有该股票，也将由于止盈止损而出局，即不买入！
        '''
       
        self.datapath = datapath
        self.unit = unit
        self.feeratio = feeratio
        self.init_cash = init_cash
        self.volratio = volratio
        self.tradeprice = tradeprice
        self.daily_limit = daily_limit
        self.min_wait_buycash = min_wait_buycash
        self.buynumber = buynumber
        self.min_holddays = min_holddays
        self.stop_type = stop_type
        self.weight_type = weight_type
        self.loss_rtn = loss_rtn
        self.gain_rtn = gain_rtn
    
    def optimize(self):
        #权重优化，今后再加
        pass
    
    
    def first_buytrade(self,buylist):
        '''
        第一天的交易
        buylist包含:TradingDay,cp,vol
        asset:资产净值
        surplus_cash:剩余资金
        tradefee:当日手续费
        stockvalue:每一只股票的市值 dataframe
        holdvol:   每一只股票持仓数量 dataframe
        wait_buycash: 每一只股票的待买金额
        weight: 买入的分配比例，根据wait_buycash做分配
        position_cash:初始买入金额，由于仓位没满，留出的部分现金
        '''
        goal_buycash = self.init_cash * buylist['weight']   
        #涨停股票权重设为0，即不买入
        buylist['weight'] =  np.where(buylist['cp']>=(1+self.daily_limit)*buylist['precp']-0.01,0,buylist['weight'])       
        real_goal_buycash = self.init_cash * buylist['weight']
        holdvol = pd.concat([np.floor(real_goal_buycash / buylist[self.tradeprice] / (1+self.feeratio) / self.unit),
                             buylist['vol'] * self.volratio],axis=1)
        holdvol = holdvol.min(axis=1)
        holddays = holdvol / holdvol
        real_buycash = holdvol * self.unit * buylist[self.tradeprice]      
        tradefee = real_buycash * self.feeratio
        wait_buycash = goal_buycash - real_buycash - tradefee
        surplus_cash = self.init_cash - real_buycash.sum() - tradefee.sum()
        stockvalue = real_buycash
        tradefee = tradefee.sum()
        asset = stockvalue.sum() + surplus_cash
        tradedetail = pd.DataFrame(goal_buycash.values,columns=['目标金额'],index=goal_buycash.index)
        tradedetail['成交数量'] =  holdvol
        tradedetail['成交金额'] =  real_buycash
        tradedetail['成交价格'] =  buylist[self.tradeprice] 
        tradedetail['剩余目标金额'] = wait_buycash
        return asset,surplus_cash,tradefee,stockvalue,holdvol,wait_buycash,holddays,tradedetail
    
    def run_trade(self,buylist,surplus_cash,holdvol,wait_buycash,sell_holdvol,sell_quote,holddays):
        '''
        非第一天及换仓第一天的交易
        min_wait_buycah: 最新的剩余资金，比若10000元，既当目标剩余资金小于10000元是，不再继续买入
        surplus_cash:剩余资金
        holdvol:T-1日持仓数量明细
        wait_buycash:每只股票剩余待买目标资金
        '''     
        tradefee = 0
        sell_tradefee = 0
        sell_stockvalue = pd.Series(0)
        sell_tradedetail = pd.DataFrame()
        tradedetail = pd.DataFrame()
        add_holdvol = pd.Series()
        holddays = holddays + 1
        #day = datetime.datetime.strftime(buylist['TradingDay'][-1],"%Y%m%d")
        buylist = buylist[buylist.index.isin(holdvol.index)] #只关注持仓中的行情
        
        #先卖出股票,腾出资金        
        if len(sell_holdvol) > 0:
            sell_quote = sell_quote[sell_quote['SecuCode'].isin(sell_holdvol.index)]
            sell_quote.index = sell_quote['SecuCode']
            surplus_cash,sell_tradefee,sell_stockvalue,sell_holdvol,day_sell_holdvol,sell_cash =  self.sell_trade(sell_holdvol,sell_quote,surplus_cash)
            if len(day_sell_holdvol) > 0:
                sell_tradedetail = pd.DataFrame(day_sell_holdvol.values,columns=['卖出成交数量'],index=day_sell_holdvol.index)
                sell_tradedetail['卖出成交金额'] = sell_cash
             

        #接下来继续交易没有完成的交易
        stockvalue = holdvol * buylist[self.tradeprice] * self.unit   
        buy_weight = wait_buycash / wait_buycash.sum() #按照未成目标金额，分配剩余资金的买入权重
        
        #如上一个交易日，由于涨停或者成交量限制，没完成交易的，继续交易    
        if wait_buycash.max() >= self.min_wait_buycash:
            goal_buycash = min(surplus_cash,wait_buycash.sum()) * buy_weight
            goal_buycash = goal_buycash.apply(lambda x: 0 if x<=self.min_wait_buycash else x)
            real_goal_buycash = self.adjust_goal_buycash(goal_buycash,buylist) #涨停股票的今日买入金额为0
            add_holdvol =  pd.concat([np.floor(real_goal_buycash / buylist[self.tradeprice] / (1+self.feeratio) / self.unit),
                                               buylist['vol'] * self.volratio],axis=1)
            add_holdvol = add_holdvol.min(axis=1)
            holdvol = holdvol + add_holdvol  
            real_buycash = add_holdvol * self.unit * buylist[self.tradeprice]
            tradefee = real_buycash * self.feeratio
            wait_buycash = wait_buycash - real_buycash - tradefee          
            stockvalue = holdvol * buylist[self.tradeprice] * self.unit
            tradefee = tradefee.sum() 
            surplus_cash = surplus_cash - real_buycash.sum() - tradefee
            
            if add_holdvol.sum()>0 or len(sell_tradedetail)>0:
                tradedetail = pd.DataFrame(wait_buycash.values,columns=['目标金额'],index=wait_buycash.index)
                tradedetail['成交数量'] =  pd.DataFrame(add_holdvol)
                tradedetail['成交金额'] =  real_buycash
                tradedetail['成交价格'] =  buylist[self.tradeprice] 
                tradedetail['剩余目标金额'] = wait_buycash
                tradedetail = tradedetail.append(sell_tradedetail)
    
        
        tradefee = sell_tradefee + tradefee
        asset = stockvalue.sum() + surplus_cash
        asset = stockvalue.sum() + sell_stockvalue.sum() + surplus_cash
            
        return asset,surplus_cash,tradefee,stockvalue,holdvol,wait_buycash,sell_holdvol,holddays,tradedetail
    
  
    
    def adjust_first_trade(self,buylist,asset,surplus_cash,stockvalue,keep_holdvol,sell_holdvol,sell_quote,holddays):
        '''
        换仓的第一天交易：
        1. 先进行权重重新分配，原有持仓中权重大的，卖出一部分股票，权重小的则按照
        2. 先卖出，然后所得的资金在用于买入
        '''
        sell_tradefee = 0
        tradefee = 0
        sell_stockvalue = pd.Series(0)
        sell_tradedetail = pd.DataFrame()
        tradedetail = pd.DataFrame()

        holddays = pd.merge(pd.DataFrame(holddays),pd.DataFrame(buylist['cp']/buylist['cp']),left_index=True,right_index=True,how='outer')
        holddays = holddays.fillna(0)
        holddays = holddays.sum(axis=1)
        holddays = holddays[holddays.index.isin(buylist.index)]
        sell_holddays = holddays[~holddays.index.isin(buylist.index)]
        
        #原有持仓，没有发出卖出信号，但权重需要重新调整
        wait_buycash = pd.DataFrame(asset * buylist['weight'])
        goal_stockvalue =  pd.DataFrame(asset * buylist['weight'])

        if len(keep_holdvol) > 0:#持仓reblance部分
            keep_stockvalue =  stockvalue[stockvalue.index.isin(keep_holdvol.index)]
            keep_stockvalue = pd.DataFrame(keep_stockvalue,index=keep_stockvalue.index,columns=['keep_stockvalue'])           
            goal_buycash = pd.merge(wait_buycash,keep_stockvalue,left_index=True,right_index=True,how='left')
            goal_buycash['keep_stockvalue'] = goal_buycash['keep_stockvalue'].fillna(0)
            goal_buycash['diff_cash'] = goal_buycash['weight'] - goal_buycash['keep_stockvalue']
            goal_buycash['holdvol_weight'] = goal_buycash['diff_cash'] / goal_buycash['keep_stockvalue']
            wait_buycash = goal_buycash
            #重新调整权重，需要卖出部分股票的程序 + 卖出股票的卖出交易
            sell_keep_holdvol_weight =   pd.DataFrame(goal_buycash[goal_buycash['holdvol_weight']<0].holdvol_weight )
            sell_keep_holdvol = pd.merge(pd.DataFrame(keep_holdvol),sell_keep_holdvol_weight,left_index=True,right_index=True,how='left')          
            sell_keep_holdvol = np.floor(sell_keep_holdvol[0] * np.abs(sell_keep_holdvol['holdvol_weight'])).dropna() 
            keep_holdvol = pd.merge(pd.DataFrame(keep_holdvol),pd.DataFrame(sell_keep_holdvol),left_index=True,right_index=True,how='left')
            keep_holdvol = keep_holdvol.fillna(0)
            keep_holdvol[0] = keep_holdvol['0_x'] - keep_holdvol['0_y']
            keep_holdvol = keep_holdvol[0]
            sell_keep_holdvol =  sell_keep_holdvol[sell_keep_holdvol>0]             
            if len(sell_holdvol) > 0:
                sell_holdvol = sell_holdvol.append(sell_keep_holdvol)
                
            else:
                sell_holdvol =   sell_keep_holdvol  
        else:
            wait_buycash['diff_cash'] = wait_buycash.values
        if len(sell_holdvol) > 0:
            sell_quote = sell_quote[sell_quote['SecuCode'].isin(sell_holdvol.index)]
            sell_quote.index=sell_quote['SecuCode']
            surplus_cash,sell_tradefee,sell_stockvalue,sell_holdvol,day_sell_holdvol,sell_cash =  self.sell_trade(sell_holdvol,sell_quote,surplus_cash)
            if len(day_sell_holdvol) > 0:
                sell_tradedetail = pd.DataFrame(day_sell_holdvol.values,columns=['卖出成交数量'],index=day_sell_holdvol.index)
                sell_tradedetail['卖出成交金额'] = sell_cash
        #新发出信号买入股票的程序
        stockvalue = keep_holdvol * buylist[self.tradeprice] * self.unit
        new_goal_buycash = wait_buycash['diff_cash']
        new_goal_buycash[new_goal_buycash<0] = 0    
        if  new_goal_buycash.max() >= self.min_wait_buycash:             
            #涨停股票权重设为0，即不买入
            buylist['weight'] =  np.where(buylist['cp']>=(1+self.daily_limit)*buylist['precp']-0.01,0,buylist['weight'])
            
            weight = new_goal_buycash  /   new_goal_buycash.sum()
            day_weight = pd.merge(pd.DataFrame(weight),buylist[['weight','SecuCode']],left_index=True,right_index=True,how='left')#涨停不买入
            day_weight[day_weight['weight']==0] = 0 #涨停的金额买入金额为0
            real_goal_buycash = min(surplus_cash,new_goal_buycash.sum()) * day_weight['diff_cash']
            add_holdvol =  pd.concat([np.floor(real_goal_buycash / buylist[self.tradeprice] / (1+self.feeratio) / self.unit),
                                         buylist['vol'] * self.volratio],axis=1)
            add_holdvol = add_holdvol.min(axis=1)
            holdvol = pd.merge(pd.DataFrame(keep_holdvol),pd.DataFrame(add_holdvol),left_index=True,right_index=True,how='outer')
            holdvol = holdvol.fillna(0)
            holdvol = holdvol.sum(axis=1)
                    
            real_buycash = add_holdvol * self.unit * buylist[self.tradeprice]
            tradefee = real_buycash * self.feeratio
            wait_buycash = new_goal_buycash - real_buycash - tradefee 
            tradefee = tradefee.sum()
            surplus_cash = surplus_cash - real_buycash.sum() - tradefee
            stockvalue = holdvol * buylist[self.tradeprice] * self.unit
            
            if add_holdvol.sum()>0 or len(sell_tradedetail)>0:
                tradedetail = pd.DataFrame(goal_stockvalue.values,columns=['目标金额'],index=goal_stockvalue.index)
                tradedetail['成交数量'] =  pd.DataFrame(add_holdvol)
                tradedetail['成交金额'] =  real_buycash
                tradedetail['成交价格'] =  buylist[self.tradeprice] 
                tradedetail['剩余目标金额'] = wait_buycash
                tradedetail = tradedetail.append(sell_tradedetail)
        else:
            holdvol = keep_holdvol
            wait_buycash = pd.Series(wait_buycash['diff_cash'].values,index=wait_buycash['diff_cash'].index)
            
        
        asset = stockvalue.sum() + sell_stockvalue.sum() + surplus_cash
        tradefee = tradefee + sell_tradefee         
        return asset,tradefee,surplus_cash,holdvol,sell_holdvol,wait_buycash,stockvalue,sell_stockvalue,holddays,sell_holddays,tradedetail
              
        
    def sell_trade(self,sell_holdvol,sell_quote,surplus_cash):
        '''
        卖出交易
        '''
        day_sell_holdvol =  pd.concat([sell_holdvol,sell_quote[['vol','cp','precp']]],axis=1) 
        day_sell_holdvol['vol'] = day_sell_holdvol['vol'] * self.volratio
        day_sell_holdvol[0] = np.where(day_sell_holdvol['cp']<=(1-self.daily_limit)*day_sell_holdvol['precp'] + 0.01
                                        ,0,day_sell_holdvol[0])  #跌停判断,跌停不卖出
        day_sell_holdvol = day_sell_holdvol.drop(labels=['cp','precp'],axis=1)
        day_sell_holdvol = day_sell_holdvol.min(axis=1)
        surplus_holdvol = sell_holdvol - day_sell_holdvol
        sell_cash = day_sell_holdvol * sell_quote[self.tradeprice] * self.unit
        sell_tradefee = (sell_cash * self.feeratio).sum()
        surplus_cash = surplus_cash + sell_cash.sum() - sell_tradefee
        sell_stockvalue = surplus_holdvol * sell_quote[self.tradeprice] * self.unit
        surplus_holdvol = surplus_holdvol[surplus_holdvol>0]
        sell_stockvalue = sell_stockvalue[sell_stockvalue>0]
        return surplus_cash,sell_tradefee,sell_stockvalue,surplus_holdvol,day_sell_holdvol,sell_cash
    
    def bonus_deal(self,day_bonus,holdvol,sell_holdvol,surplus_cash,asset): 
        '''
        分红行权处理
        '''   
        if len(holdvol) > 0:                 
            new_holdvol = pd.merge(pd.DataFrame(holdvol),day_bonus[['SecuCode','BonusShareRatio','TranAddShareRaio','CashDiviRMB']],\
                                left_index=True,right_on=['SecuCode'],how='left')
            new_holdvol =  new_holdvol.fillna(0)
            new_holdvol.index = holdvol.index
            holdvol = new_holdvol[0] *(1+new_holdvol['BonusShareRatio']/10+new_holdvol['TranAddShareRaio']/10)
            bonus_cash = (new_holdvol[0] * new_holdvol['CashDiviRMB']*self.unit/10).sum()
            surplus_cash = surplus_cash + bonus_cash
            asset = asset + bonus_cash
        if len(sell_holdvol) > 0:
            new_sell_holdvol = pd.merge(pd.DataFrame(sell_holdvol),day_bonus[['SecuCode','BonusShareRatio','TranAddShareRaio','CashDiviRMB']],\
                                left_index=True,right_on=['SecuCode'],how='left')
            new_sell_holdvol =  new_sell_holdvol.fillna(0)
            new_sell_holdvol.index = sell_holdvol.index
            sell_holdvol = new_sell_holdvol[0] *(1+new_sell_holdvol['BonusShareRatio']/10+new_sell_holdvol['TranAddShareRaio']/10)
            bonus_cash =  (new_sell_holdvol[0] * new_sell_holdvol['CashDiviRMB']*self.unit/10).sum()
            surplus_cash = surplus_cash + bonus_cash
            asset = asset + bonus_cash
        return holdvol,sell_holdvol,surplus_cash,asset
     
    def adjust_goal_buycash(self,goal_buycash,buylist):
        '''
        调整买入目标金额，当今天涨停时，则今日买入金额为0
        '''
        goal_buycash = pd.DataFrame(goal_buycash.values,index=goal_buycash.index,columns=['goal_buycash'])
        goal_buycash = pd.merge(goal_buycash,buylist,left_index=True,right_index=True,how='left')
        goal_buycash['goal_buycash'] = np.where(goal_buycash['cp']>=(1+self.daily_limit)*goal_buycash['precp']-0.01,0,goal_buycash['goal_buycash'])
        return goal_buycash['goal_buycash']   
    
    
    def daily_loss_gain(self,fq_cp,trade_fq_cp,day_benchmark_quote,trade_benchmark_cp,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays):
        '''
        止盈止损的调整
        发生止盈、止损需要卖出的情况，则进行相应调整
        '''
        sell_stock = pd.Series()
        if self.stop_type == 'relative': #相对收益止盈、止损
            if self.loss_rtn is not None: #止损
                dail_sy = fq_cp / trade_fq_cp - 1
                alpha_sy = dail_sy -  (day_benchmark_quote['precp'][0] / trade_benchmark_cp-1)
                sell_stock = alpha_sy[alpha_sy<=self.loss_rtn] 
                #day_buylist = day_buylist[~day_buylist.index.isin(loss_stock.index)]
                sell_holdvol = sell_holdvol.append(holdvol[sell_stock.index])
                sell_holdvol = sell_holdvol.groupby(sell_holdvol.index).sum()
                holdvol = holdvol[~holdvol.index.isin(sell_stock.index)]
                stockvalue =  stockvalue[~stockvalue.index.isin(sell_stock.index)]
                wait_buycash = wait_buycash[~wait_buycash.index.isin(sell_stock.index)]
                holddays = holddays[~holddays.index.isin(sell_stock.index)]
                trade_benchmark_cp = trade_benchmark_cp[holdvol.index]
                trade_fq_cp = trade_fq_cp[holdvol.index]
         
            if self.gain_rtn is not None: #止盈
                dail_sy = fq_cp / trade_fq_cp - 1
                alpha_sy = dail_sy -  (day_benchmark_quote['precp'][0] / trade_benchmark_cp-1)
                sell_stock = alpha_sy[alpha_sy>=self.gain_rtn] 
                #day_buylist = day_buylist[~day_buylist.index.isin(gain_stock.index)]                        
                sell_holdvol = sell_holdvol.append(holdvol[sell_stock.index])
                sell_holdvol = sell_holdvol.groupby(sell_holdvol.index).sum()
                holdvol = holdvol[~holdvol.index.isin(sell_stock.index)]
                stockvalue =  stockvalue[~stockvalue.index.isin(sell_stock.index)]
                wait_buycash = wait_buycash[~wait_buycash.index.isin(sell_stock.index)]
                holddays = holddays[~holddays.index.isin(sell_stock.index)]
                trade_benchmark_cp = trade_benchmark_cp[holdvol.index]
                trade_fq_cp = trade_fq_cp[holdvol.index]
              
        elif self.stop_type == 'abs': #绝对止盈、止损
            if self.loss_rtn is not None: #止损
                dail_sy = fq_cp / trade_fq_cp - 1
                sell_stock = dail_sy[dail_sy<=self.loss_rtn] 
                #day_buylist = day_buylist[~day_buylist.index.isin(loss_stock.index)]
                sell_holdvol = sell_holdvol.append(holdvol[sell_stock.index])
                sell_holdvol = sell_holdvol.groupby(sell_holdvol.index).sum()
                holdvol = holdvol[~holdvol.index.isin(sell_stock.index)]
                stockvalue =  stockvalue[~stockvalue.index.isin(sell_stock.index)]
                wait_buycash = wait_buycash[~wait_buycash.index.isin(sell_stock.index)]
                holddays = holddays[~holddays.index.isin(sell_stock.index)]
                trade_benchmark_cp = trade_benchmark_cp[holdvol.index]
                trade_fq_cp = trade_fq_cp[holdvol.index]
             
            if self.gain_rtn is not None: #止盈
                dail_sy = fq_cp / trade_fq_cp - 1
                sell_stock = dail_sy[dail_sy>=self.gain_rtn] 
                #day_buylist = day_buylist[~day_buylist.index.isin(gain_stock.index)]
                sell_holdvol = sell_holdvol.append(holdvol[sell_stock.index])
                sell_holdvol = sell_holdvol.groupby(sell_holdvol.index).sum()
                holdvol = holdvol[~holdvol.index.isin(sell_stock.index)]
                stockvalue =  stockvalue[~stockvalue.index.isin(sell_stock.index)]
                wait_buycash = wait_buycash[~wait_buycash.index.isin(sell_stock.index)]
                holddays = holddays[~holddays.index.isin(sell_stock.index)]
                trade_benchmark_cp = trade_benchmark_cp[holdvol.index]
                trade_fq_cp = trade_fq_cp[holdvol.index]
            
        #------------------------------------------------------------------------------------- 
        return sell_stock,trade_fq_cp,trade_benchmark_cp,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays                  
    
    def settle(self,buylist,benchmark_code,enddate):   
        '''
        buy_list:每次的选股清单，datetime格式的index,innercode,secucode,secuabbr,signal_rank
        quote：行情,需要至少包括TradingDay,secucode,cp,precp,vol字段
        bonus: 分红数据,至少需要包括ExDiviDate:行权日期, BonusShareRatio:送股比例(10送X),
                TranAddShareRaio:转增股比例(10转增X),CashDiviRMB:派现(含税/人民币元) 字段
        每日清算程序
        benchmark:基准行情，index为日期格式，index_cp,收盘价行情
        '''
        timeindex = pd.DataFrame(buylist.index.drop_duplicates().values)
        timeindex = timeindex[timeindex[0]<=enddate]
        timeindex['date'] = timeindex[0].apply(lambda x:datetime.datetime.strftime(x,"%Y%m%d"))
        starttime = timeindex['date'].min() 
        #获取分红数据
        bonus =  pd.read_hdf(self.datapath,'bonus', columns=['ExDiviDate','SecuCode','BonusShareRatio','TranAddShareRaio','CashDiviRMB'],
                             where='ExDiviDate>=%s & ExDiviDate<=%s'%(starttime,enddate))         
        #获取基准行情数据
        benchmark =  pd.read_hdf(self.datapath,'index_quote',columns=['TradingDay','SecuCode','cp','precp'],
                                     where='SecuCode in %s & TradingDay>%s & TradingDay<=%s'%(str(tuple((benchmark_code,'000'))),starttime,enddate))           
        benchmark.index = benchmark['TradingDay']
        

        asset0 = []
        holdvol0 = pd.DataFrame()
        stockvalue0 = pd.DataFrame()
        sell_holdvol0 = pd.DataFrame()
        wait_buycash0 = pd.DataFrame()
        holddays0 = pd.DataFrame()
        sell_holddays0 = pd.DataFrame()
        tradedetail0 = pd.DataFrame()
        buytime = pd.DataFrame()
        selltime = pd.DataFrame()
        day1 = []
        holddays = pd.Series()
        
        for i in tqdm(range(len(timeindex))):
            nowtime =  timeindex.iloc[i]['date'] 
            if i == len(timeindex) - 1:
                nexttime = enddate
            else:
                nexttime =  timeindex.iloc[i+1]['date']
  
            day_buy = buylist[buylist.index==nowtime]
            day_buy.index = day_buy['SecuCode']
            #当买入的天数不足最小持有天数数，则本次调仓不卖出，把不卖出的股票添加到买入列表，并且signal_rank设置成最小，既最优先买入
            cannot_sellstock = holddays[holddays<=self.min_holddays]
            buystock = day_buy.index
            if len(cannot_sellstock) > 0:
                buystock =  cannot_sellstock.index.append(day_buy.index)
            
            #获取本次调仓时间到下次调仓时间的数据
            session_quote = pd.read_hdf(self.datapath,'equity_quote',columns=['TradingDay','SecuCode','cp','precp','fq_cp','vol'],
                                     where='TradingDay>%s & TradingDay<=%s'%(nowtime,nexttime))  
            buy_quote = session_quote[session_quote['SecuCode'].isin(buystock)]
            
           
            buy_quote = pd.merge(buy_quote,day_buy.drop(['TradingDay','SecuAbbr'],axis=1),on=['SecuCode'],how='left')
            buy_quote['signal_rank'] = buy_quote['signal_rank'].fillna(0)
            buy_quote.index  =buy_quote['TradingDay']
            
            session_timeindex = buy_quote[['TradingDay']].drop_duplicates()
            session_timeindex = session_timeindex.sort_index()
            
            if i == 0: #第一次交易，仅有买入
                for day in session_timeindex.index:
                    day_buylist = buy_quote[buy_quote['TradingDay']==day]
                    day_buylist.index = day_buylist['SecuCode'] 
                    day_benchmark_quote = benchmark[benchmark['TradingDay']==day] 
                    
                                                
                    #第一天的处理
                    if day == session_timeindex.index.min():  
                        day_buylist['signal_rank'] = day_buylist['signal_rank'].rank(method='first')    
                        day_buylist = day_buylist[day_buylist['signal_rank']<=self.buynumber]
                        if self.weight_type == None:
                            day_buylist['weight'] = 1.0 / len(day_buylist)
                        sell_stockvalue =  pd.Series(0)      
                        trade_fq_cp = day_buylist['fq_cp'] #记录复权成交价 
                        fq_cp = trade_fq_cp
                        trade_benchmark_cp = (1+trade_fq_cp - trade_fq_cp) *day_benchmark_quote['cp'][0] #记录基准价格
                        asset,surplus_cash,tradefee,stockvalue,holdvol,wait_buycash,holddays,tradedetail = self.first_buytrade(day_buylist)                          
                        sell_holdvol = pd.Series()  
                    else:  
                        if len(session_timeindex)>2:#防止两个调仓日期仅差一天，出现BUG的情况
                            sell_quote = session_quote[session_quote['TradingDay']==day]                    
                            #---daily止盈止损-------------------------------------------------------------------       
                            if self.stop_type is not None:
                                sell_stock,trade_fq_cp,trade_benchmark_cp,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays = \
                                    self.daily_loss_gain(fq_cp,trade_fq_cp,day_benchmark_quote,trade_benchmark_cp 
                                             ,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays) 
                                
                            day_bonus = bonus[bonus['ExDiviDate']==day]
                            day_bonus.index = day_bonus['SecuCode']
                            day_bonus = day_bonus[(day_bonus.index.isin(holdvol.index))|(day_bonus.index.isin(sell_holdvol.index))]
                            #分红处理
                            if len(day_bonus) > 0:                        
                                holdvol,sell_holdvol,surplus_cash,asset = self.bonus_deal(day_bonus,holdvol,sell_holdvol,surplus_cash,asset)
                            asset,surplus_cash,tradefee,stockvalue,holdvol,wait_buycash,sell_holdvol,holddays,tradedetail = \
                            self.run_trade(day_buylist,surplus_cash,holdvol,wait_buycash,sell_holdvol,sell_quote,holddays)
                            fq_cp = day_buylist.ix[holdvol.index]['cp'] #记录现有持仓前实盘价
                            
                        
                    asset0.append([asset,tradefee,surplus_cash,stockvalue.sum(),sell_stockvalue.sum()])
                    holdvol0 = holdvol0.append(pd.DataFrame(holdvol))
                    stockvalue0 = stockvalue0.append(pd.DataFrame(stockvalue))
                    holddays0 = holddays0.append(pd.DataFrame(holddays))
                    buytime = buytime.append(pd.DataFrame(len(holdvol)*[day]))
                    wait_buycash0 = wait_buycash0.append(pd.DataFrame(wait_buycash))
                    tradedetail['日期'] =  np.array(len(tradedetail) * [day])
                    tradedetail0 = tradedetail0.append(tradedetail)
                    day1.append(day)
                   
                        
            else:                 
                for day in session_timeindex.index:
                    #print(day)
                    day_buylist = buy_quote[buy_quote['TradingDay']==day]
                    day_buylist.index = day_buylist['SecuCode'] 
                    day_benchmark_quote = benchmark[benchmark['TradingDay']==day] 
                    day_bonus = bonus[bonus['ExDiviDate']==day]
                    day_bonus.index = day_bonus['SecuCode']
                    sell_quote = session_quote[session_quote['TradingDay']==day]
                    #分红处理  
                    try:
                        day_bonus = day_bonus[(day_bonus.index.isin(holdvol.index))|(day_bonus.index.isin(sell_holdvol.index))]
                    except:
                        day_bonus = day_bonus[(day_bonus.index.isin(holdvol.index))]
                                                
                    if len(day_bonus) > 0:                        
                        holdvol,sell_holdvol,surplus_cash,asset = self.bonus_deal(day_bonus,holdvol,sell_holdvol,surplus_cash,asset)                          
                    
                    #换仓日的第一天交易
                    if day == session_timeindex['TradingDay'].min():
                        #发生止盈止损的股票，即使本次有新信号产生，也不买入
                        sell_stock = pd.Series()
                        if self.stop_type is not None:
                            sell_stock,trade_fq_cp,trade_benchmark_cp,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays = \
                                    self.daily_loss_gain(fq_cp,trade_fq_cp,day_benchmark_quote,trade_benchmark_cp
                                                     ,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays) 
                           
                        day_buylist = day_buylist[~day_buylist.index.isin(sell_stock.index)]  
                         #持仓中在本次股票池中，则signal_rank=0,相当于不卖出  
                        day_buylist['signal_rank'] = np.where(day_buylist.index.isin(holdvol.index)==True,0,day_buylist['signal_rank'])
                        day_buylist['signal_rank'] = day_buylist['signal_rank'].rank(method='first')    
                        day_buylist = day_buylist[day_buylist['signal_rank']<=self.buynumber]
                        if self.weight_type == None:
                            day_buylist['weight'] = 1.0 / day_buylist.count()[0]
                        #进行止盈止损操作,，仍在买入目标池中股票，计算当前的收益
                        keep_holdvol = holdvol[holdvol.index.isin(day_buylist.index)]
                        old_sell_holdvol = pd.DataFrame()
                        try:
                            if len(sell_holdvol)>0: #待卖出的股票中，有需要买入的，则取消卖出                          
                                keep_sell_holdvol = sell_holdvol[sell_holdvol.index.isin(day_buylist.index)]
                                old_sell_holdvol =pd.DataFrame(sell_holdvol[~sell_holdvol.index.isin(day_buylist.index)])  
                                if len(keep_sell_holdvol) > 0:#未卖出部分转移至保持股票列表
                                    keep_holdvol = pd.merge(pd.DataFrame(keep_holdvol),pd.DataFrame(keep_sell_holdvol),left_index=True,right_index=True,how='outer')
                                    keep_holdvol = keep_holdvol.fillna(0)
                                    keep_holdvol = keep_holdvol.sum(axis=1)
                                    keep_sell_stockvalue = sell_stockvalue[sell_stockvalue.index.isin(day_buylist.index)]
                                    stockvalue = pd.merge(pd.DataFrame(stockvalue),pd.DataFrame(keep_sell_stockvalue),left_index=True,right_index=True,how='outer')
                                    stockvalue = stockvalue.fillna(0)
                                    stockvalue = stockvalue.sum(axis=1)
                        except:
                            pass
                        
                        finally:                           
                            if len(sell_holdvol) > 0:
                                new_sell_holdvol = holdvol[~holdvol.index.isin(day_buylist.index)] 
                                sell_holdvol = pd.merge(old_sell_holdvol,pd.DataFrame(new_sell_holdvol),left_index=True,right_index=True,how='outer')
                                sell_holdvol = sell_holdvol.fillna(0)
                                sell_holdvol = sell_holdvol.sum(axis=1)
                            else:
                                sell_holdvol = holdvol[~holdvol.index.isin(day_buylist.index)] 
                            asset,tradefee,surplus_cash,holdvol,sell_holdvol,wait_buycash,stockvalue,sell_stockvalue,holddays,sell_holddays,tradedetail = \
                            self.adjust_first_trade(day_buylist,asset,surplus_cash,stockvalue,keep_holdvol,sell_holdvol,sell_quote,holddays)
           
                            
                            new_trade_stock = holdvol[~holdvol.index.isin(trade_fq_cp.index)].index
                            new_trade_stock =  day_buylist[day_buylist.index.isin(new_trade_stock)]['cp']
                            trade_fq_cp = trade_fq_cp.append(new_trade_stock) #新买入股票，记录买入成本价
                            trade_benchmark_cp = trade_benchmark_cp.append((1+new_trade_stock-new_trade_stock)*day_benchmark_quote['cp'][0])
                            fq_cp = day_buylist.ix[holdvol.index]['cp']
                            sell_holddays0 = sell_holddays0.append(pd.DataFrame(sell_holddays))
                       
                    else:
                        if len(session_timeindex)>2:#防止调仓日期仅差一个交易而传输的bug
                            if self.stop_type is not None:
                                sell_stock,trade_fq_cp,trade_benchmark_cp,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays = \
                                    self.daily_loss_gain(fq_cp,trade_fq_cp,day_benchmark_quote,trade_benchmark_cp
                                                     ,sell_holdvol,holdvol,stockvalue,wait_buycash,holddays) 
                            asset,surplus_cash,tradefee,stockvalue,holdvol,wait_buycash,sell_holdvol,holddays,tradedetail = \
                            self.run_trade(day_buylist,surplus_cash,holdvol,wait_buycash,sell_holdvol,sell_quote,holddays)
                            fq_cp = day_buylist.ix[holdvol.index]['cp']
                    
                    
                    asset0.append([asset,tradefee,surplus_cash,stockvalue.sum(),sell_stockvalue.sum()])
                    holdvol0 = holdvol0.append(pd.DataFrame(holdvol))
                    stockvalue0 = stockvalue0.append(pd.DataFrame(stockvalue))
                    sell_holdvol0 = sell_holdvol0.append(pd.DataFrame(sell_holdvol))
                    holddays0 = holddays0.append(pd.DataFrame(holddays))
                    buytime = buytime.append(pd.DataFrame(len(holdvol)*[day]))
                    selltime = selltime.append(pd.DataFrame(len(sell_holdvol)*[day]))
                    wait_buycash0 = wait_buycash0.append(pd.DataFrame(wait_buycash))
                    tradedetail['日期'] =  np.array(len(tradedetail) * [day])
                    tradedetail0 = tradedetail0.append(tradedetail)
                    day1.append(day)
                   
        
        #每日的持仓明细 
        index2 = buytime.drop_duplicates()[0]
        asset = pd.DataFrame(np.array(asset0),index=day1,columns=['净值','手续费','现金','持有证券资产','待卖证券资产'])
        
        holdvol0['SecuCode'] = holdvol0.index
        holdvol0['市值'] =  np.array(stockvalue0 )
        holdvol0['天数'] =  np.array(holddays0 )
        holdvol0['目标金额'] = np.array( wait_buycash0 )
        holdvol0['date']  =  np.array(buytime[0])
       
         #插入代码和简称
        name_info = buylist.drop_duplicates(subset=['SecuCode'])[['SecuCode','SecuAbbr']]
        name_info.index = range(len(name_info))
        name_info = name_info.drop_duplicates()

        holdvol = pd.merge(name_info,holdvol0,on='SecuCode',how='right')
        holdvol = pd.DataFrame(np.array(holdvol),columns=['证券代码','证券简称','持仓数量','市值','天数','目标金额','日期'])
          
        if len(selltime) > 0:
           sell_holdvol0['SecuCode'] =  sell_holdvol0.index
           sell_holdvol0['date'] = np.array(selltime[0])
           sell_holdvol = pd.merge(name_info,sell_holdvol0,on='SecuCode',how='right')
           sell_holdvol = pd.DataFrame(np.array(sell_holdvol),columns=['证券代码','证券简称','持仓数量','日期'])

        else:
            sell_holdvol = pd.DataFrame(columns=['证券代码','证券简称','持仓数量','日期'])

        tradedetail0 = pd.merge(name_info,tradedetail0,right_index=True,left_on='SecuCode',how='right')      
        
        #保存成交明细到excel
#        with pd.ExcelWriter("C:\\py_data\\textdata\\text.xlsx") as writer:
#            asset.to_excel(writer,u'asset')           
#            sell_holdvol.to_excel(writer,'待卖出股票情况')           
#            sell_holddays0.to_excel(writer,'股票持仓天数统计')
#            tradedetail0.to_excel(writer,"每日成交明细")
#            holdvol.to_excel(writer,"每日持仓明细")
        
        return asset,holdvol,sell_holdvol,tradedetail0,sell_holddays0 
    

    def performance_compute(self,alpha,benchmark_sy,jz_name,alpha_year_sy2=None)   :
        '''
        alpha:log格式的rtn
        jz_name: str "超额收益[沪深300]" 或者"对冲收益"
        '''
        alpha = pd.DataFrame(np.log(1+alpha))
        alpha_nav =  pd.DataFrame(np.exp(alpha.cumsum()).values,index=alpha.index,columns=[jz_name]) - 1
        alpha_sy = 100 * (pow(np.exp(alpha.sum()),250/alpha.count()) - 1 )
        alpha_sum_sy = 100 *(np.exp(alpha.sum()) - 1)
        alpha_asset = np.exp(alpha.cumsum())
        alpha_max = alpha_asset.cummax()
        alpha_maxdrawdown = 100 * (alpha_asset/alpha_max - 1).min()
        alpha_std = 100 * (np.exp(alpha)-1).std() * np.sqrt(250)
        info_ratio = alpha_sy / alpha_std
        alpha_m_rtn = np.exp(alpha.resample('m').sum())-1
        alpha_m_ratio =  100 *alpha_m_rtn[alpha_m_rtn>0].count()/alpha_m_rtn.count()
        alpha_win_vs_loss = 100 * (-1*alpha_m_rtn[alpha_m_rtn>0].mean() / alpha_m_rtn[alpha_m_rtn<0].mean())
        
        alpha_performance = pd.DataFrame(np.array([benchmark_sy,alpha_sum_sy,alpha_sy,alpha_std,alpha_maxdrawdown,info_ratio,alpha_m_ratio,alpha_win_vs_loss]),
                               index=['总收益/基准累计收益','总收益/超额','年化收益率/超额','年化波动率/超额','最大回撤/超额','夏普/信息比率','月度胜利率/超额','月度平均盈亏比/超额'])
        alpha_performance = alpha_performance.apply(lambda x:round(x,2))
        alpha_group_year = alpha.groupby(lambda x:x.year)
        alpha_group_rtn = np.exp(alpha_group_year.cumsum())
        alpha_group_rtn = alpha_group_rtn / alpha_group_rtn.groupby(lambda x:x.year).cummax() - 1
        alpha_year_maxdrawdown = 100 *alpha_group_rtn.groupby(lambda x:x.year).min()
        alpha_year_sy = 100 * (np.exp(alpha_group_year.sum()) - 1)
        if alpha_year_sy2 is not None:
            alpha_year_sy = alpha_year_sy2 
        alpha_year_sy[0] = alpha_year_sy[0].apply(lambda x :str('%.2f'%x))
        alpha_year_maxdrawdown[0] = alpha_year_maxdrawdown[0].apply(lambda x :str('%.2f'%x))
        alpha_year_sy[0] =  list(zip(alpha_year_sy[0],alpha_year_maxdrawdown[0]))
        
        #alpha_year_sy = alpha_year_sy.drop([0],axis=1)      
        alpha_month_sy = pd.DataFrame(100 * (np.exp(alpha.resample('m').sum())-1))   
        alpha_month_sy = pd.DataFrame(alpha_month_sy.values,index=alpha_month_sy.index,columns=[jz_name])
        alpha_performance = alpha_performance.append(alpha_year_sy)
        alpha_performance = pd.DataFrame(alpha_performance.values,index=alpha_performance.index,columns=[jz_name])
        return alpha_nav,alpha_month_sy,alpha_performance
            
    def performance(self,data,sort_name,benchmark_code=None):
        '''
        策略绩效汇总
        '''
        asset = data[['净值']]  
        max_asset = asset.cummax()
        loss_from_max = asset / max_asset - 1 
        maxdrawdown = 100 * loss_from_max.min()
        rtn = np.log(asset/asset.shift(1))
        rtn = rtn.fillna(0) #第一天的处理
        m_nav = np.exp(rtn.cumsum())  
        sum_sy = 100 *(np.exp(rtn.sum()) - 1)
        std = 100 * (np.exp(rtn)-1).std() * np.sqrt(250)
        sy = 100 * (pow(np.exp(rtn.sum()),250/rtn.count()) - 1 )
        sharpratio = (sy - 3) / std
        m_rtn = np.exp(rtn.resample('m').sum()) - 1
        m_ratio = 100 *m_rtn[m_rtn>0].count()/m_rtn.count()
        win_vs_loss = 100 * (-1*m_rtn[m_rtn>0].mean() / m_rtn[m_rtn<0].mean())
        turnover = 100 * (data['手续费']/self.feeratio/data['净值']).sum()*250/rtn.count() / 2
        
        performance = pd.DataFrame(np.array([sum_sy,sum_sy,sy,std,maxdrawdown,sharpratio,turnover,m_ratio,win_vs_loss]),
                                   index=['总收益/基准累计收益','总收益/超额','年化收益率/超额','年化波动率/超额','最大回撤/超额','夏普/信息比率','年化换手率','月度胜利率/超额','月度平均盈亏比/超额'])
        performance = performance.apply(lambda x:round(x,2))                          
        group_year = rtn.groupby(lambda x:x.year)
        group_rtn = np.exp(group_year.cumsum())
        group_rtn  = group_rtn / group_rtn.groupby(lambda x:x.year).cummax() - 1
        year_maxdrawdown = 100 *(group_rtn.groupby(lambda x:x.year).min())
        year_sy = 100 * (np.exp(group_year.sum()) - 1)
        temp_year_sy = pd.DataFrame(np.array(year_sy),index=year_sy.index)
        year_sy['净值'] = year_sy['净值'].apply(lambda x :str('%.2f'%x))
        year_maxdrawdown['净值'] = year_maxdrawdown['净值'].apply(lambda x :str('%.2f'%x))
        year_sy[0] =  list(zip(year_sy['净值'],year_maxdrawdown['净值']))
        year_sy = year_sy.drop(['净值'],axis=1)
        month_sy = pd.DataFrame(100 * (np.exp(rtn.resample('m').sum()) - 1))   
        #每年换手率情况
        turnover_group = 100 * (data['手续费']/self.feeratio/data['净值']) / 2
        year_turnover_group = turnover_group.groupby(lambda x:x.year)
        year_turnover = year_turnover_group.sum()*250/year_turnover_group.count()
        year_turnover = pd.DataFrame(year_turnover.rename('年化换手率%'))
        year_turnover['实际换手率%'] =  year_turnover_group.sum()
        #月换手率情况
        month_turnover = turnover_group.resample('m').sum()
        month_turnover = pd.DataFrame(month_turnover.rename('换手率%'))
        month_sy = pd.merge(month_sy,month_turnover,left_index=True,right_index=True,how='left')         
        performance = performance.append(year_sy)
        
        if benchmark_code is not None:
            starttime = datetime.datetime.strftime(asset.index.min(),"%Y%m%d")
            endtime =  datetime.datetime.strftime(asset.index.max(),"%Y%m%d")
            benchmark_data =  pd.read_hdf(self.datapath,'index_quote',columns=['TradingDay','SecuCode','SecuAbbr','cp'],
                                     where='SecuCode in %s & TradingDay>=%s & TradingDay<=%s'%(str(tuple((benchmark_code,'000'))),starttime,endtime))     
            benchmakr_name = benchmark_data['SecuAbbr'].iloc[0]
            benchmark_data.index  =benchmark_data['TradingDay']
            benchmark_data = benchmark_data.sort_index() #按照时间顺序排序
            benchmark_rtn = np.log(benchmark_data['cp'] / benchmark_data['cp'].shift(1))  
            benchmark_sy = 100 *(np.exp(benchmark_rtn.sum()) - 1)
            benchmark_nav = pd.DataFrame(np.exp(benchmark_rtn.fillna(0).cumsum()))
            alpha = 1 + asset['净值'] /asset['净值'].iloc[0] - benchmark_data['cp']/benchmark_data['cp'].iloc[0]
            alpha = alpha/alpha.shift(1) -  1
            alpha = alpha.fillna(0)
            group_year2 = benchmark_rtn.groupby(lambda x:x.year) 
            alpha_year_sy2 =  100 * (np.exp(group_year2.sum()) - 1) #每年超额收益
            alpha_year_sy2 = pd.merge(temp_year_sy,pd.DataFrame(alpha_year_sy2),left_index=True,right_index=True)
            alpha_year_sy2[0] = alpha_year_sy2[0] - alpha_year_sy2['cp']
            alpha_year_sy2 = alpha_year_sy2[[0]]
            
            alpha2 = asset['净值'] /asset['净值'].shift(1) - benchmark_data['cp']/benchmark_data['cp'].shift(1)
            alpha2 = alpha2.fillna(0)
            
            alpha_nav1,alpha_month_sy,alpha_performance = self.performance_compute(alpha,benchmark_sy,'超额收益',alpha_year_sy2)
            alpha_nav2,alpha_month_sy2,alpha_performance2 = self.performance_compute(alpha2,benchmark_sy,'多空收益')
            

            performance = pd.merge(performance,alpha_performance,left_index=True,right_index=True,how='outer')
            performance = pd.merge(performance,alpha_performance2,left_index=True,right_index=True,how='left')
            month_sy = pd.merge(month_sy,alpha_month_sy,left_index=True,right_index=True)
            month_sy = pd.merge(month_sy,alpha_month_sy2,left_index=True,right_index=True)
            m_nav = pd.merge(m_nav,benchmark_nav,left_index=True,right_index=True)
            m_nav = pd.merge(m_nav,alpha_nav1,left_index=True,right_index=True)
            m_nav = pd.merge(m_nav,alpha_nav2,left_index=True,right_index=True)
            performance.columns = ['策略','超额收益[%s]'%benchmakr_name,'多空收益[%s]'%benchmakr_name]
            m_nav.columns = ['策略收益','%s收益'%benchmakr_name,'超额收益[%s]'%benchmakr_name,'多空收益[%s]'%benchmakr_name]    
        #保存到excel
        with pd.ExcelWriter("C:\\py_data\\textdata\\%s_performance.xlsx"%sort_name) as writer:            
            performance.to_excel(writer,"策略表现")
            m_nav.to_excel(writer,"收益率曲线")
            month_sy.to_excel(writer,"每月收益")     
            year_turnover.to_excel(writer,"换手率情况")
            
        
        return performance,m_nav                                    
if __name__ == "__main__":
##------------运行模拟程序选股------------------------------------------------------------------
    trade_func = trade(
                datapath = "C:\\py_data\\datacenter\\quote.h5",
                unit = 100,
                feeratio = 0.003,
                init_cash = 100000000,
                volratio = 1,
                tradeprice = 'cp',
                daily_limit = 0.1 ,
                min_wait_buycash = 10000,
                buynumber = 10,
                min_holddays = 1,
                stop_type = None,
                weight_type =  None,
                loss_rtn = None,
                gain_rtn=None)

    #net,holdvol,sell_holdvol,tradedetail0,sell_holddays0 = trade_func.settle(buylist2,"399905",'20171030')
    #performance,m_nav = trade_func.performance(net,"399905")     

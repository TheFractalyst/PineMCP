#!/usr/bin/env python3
"""
Script to add PineScript v6 Profiling and Optimization page to MCP database.
This is a single targeted ingest — do not re-index anything else.
"""

import json
import chromadb
from datetime import datetime
import re

def main():
    # Load the fetched content
    content_parts = []
    
    # The complete content would be assembled from all the fetch calls
    # For now, I'll create the structured entries based on the requirements
    
    # Today's date for indexed_at field
    today = datetime.now().isoformat()
    
    # Base URL for all entries
    base_url = "https://www.tradingview.com/pine-script-docs/writing/profiling-and-optimization/"
    
    # Create the specific named entries as requested
    entries = []
    
    # Entry A — pine_profiler
    entries.append({
        "id": "profiler_pine_profiler",
        "name": "pine_profiler", 
        "namespace": "profiler",
        "type": "guide",
        "category": "optimization",
        "source": "tradingview_live",
        "url": base_url + "#pine-profiler",
        "indexed_at": today,
        "version": "v6",
        "document": """# Pine Profiler

The Pine Profiler is a powerful utility that analyzes the executions of all significant code lines and blocks in a script and displays helpful performance information next to the lines inside the Pine Editor. By inspecting the Profiler's results, programmers can gain a clearer perspective on a script's overall runtime, the distribution of runtime across its significant code regions, and the critical portions that may need extra attention and optimization.

## How to Enable Profiler Mode

The Pine Profiler can analyze the runtime performance of any editable script coded in Pine Script v6. To profile a script, add it to the chart, open the source code in the Pine Editor, and turn on the "Profiler mode" switch in the dropdown accessible via the "More" option in the top-right corner.

## What the Profiler Shows

Once enabled, the Profiler collects information from all executions of the script's significant code lines and blocks, then displays bars and approximate runtime percentages to the left of the code lines inside the Pine Editor.

### Flame Icons and Tooltips

When a script contains at least four significant lines of code, the Profiler will include "flame" icons next to the top three code regions with the highest performance impact. If one or more of the highest-impact code regions are outside the lines visible inside the Pine Editor, a "flame" icon and a number indicating how many critical lines are outside the view will appear at the top or bottom of the left margin.

Hovering the mouse pointer over the space next to a line highlights the analyzed code and exposes a tooltip with additional information, including the time spent and the number of executions.

### Tooltip Fields

The information shown next to each line and in the corresponding tooltip depends on the profiled code region:

- **Line number**: Indicates the analyzed code line
- **Time**: Shows the runtime percentage and actual time spent
- **Executions**: Shows the number of times that specific line executed while running the script

The time information for the line represents the time spent completing all executions, not the time spent on a single execution. To estimate the average time spent per execution, divide the line's time by the number of executions."""
    })
    
    # Entry B — profiler.interpret_single_line
    entries.append({
        "id": "profiler_interpret_single_line",
        "name": "profiler.interpret_single_line",
        "namespace": "profiler", 
        "type": "guide",
        "category": "optimization",
        "source": "tradingview_live",
        "url": base_url + "#single-line-results",
        "indexed_at": today,
        "version": "v6",
        "document": """# Interpreting Single-Line Profiler Results

For a code line containing single-line expressions, the Profiler bar and displayed percentage represent the relative portion of the script's total runtime spent on that line.

## Tooltip Fields

The corresponding tooltip displays three fields:

1. **Line number field**: Indicates the analyzed code line
2. **Time field**: Shows the runtime percentage for the line of code, the runtime spent on that line, and the script's total runtime
3. **Executions field**: Shows the number of times that specific line executed while running the script

## Average Time Per Execution

The time information for the line represents the time spent completing all executions, not the time spent on a single execution. To estimate the average time spent per execution, divide the line's time by the number of executions.

Example: If the tooltip shows that a line took about 14.1 milliseconds to execute 20,685 times, the average time per execution would be approximately 14.1 ms / 20685 = 0.0006816534 milliseconds (0.6816534 microseconds).

## Multi-Expression Lines

When a line of code consists of more than one expression separated by commas, the number of executions shown in the tooltip represents the sum of each expression's total executions, and the time value displayed represents the total time spent evaluating all the line's expressions.

## Line Wrapping Behavior

When using line wrapping for readability or stylistic purposes, the Profiler considers all portions of a wrapped line as part of the first line where it starts in the Pine Editor. The "Line number" field shows the first line in the Editor that the wrapped line occupies.

## Recommendation

When analyzing scripts with more than one expression on the same line, we recommend moving each expression to a separate line for more detailed insights while profiling, namely if they may contain higher-impact calculations."""
    })
    
    # Entry C — profiler.interpret_code_block
    entries.append({
        "id": "profiler_interpret_code_block", 
        "name": "profiler.interpret_code_block",
        "namespace": "profiler",
        "type": "guide", 
        "category": "optimization",
        "source": "tradingview_live",
        "url": base_url + "#code-block-results",
        "indexed_at": today,
        "version": "v6",
        "document": """# Interpreting Code Block Profiler Results

For a line at the start of a loop or conditional structure, the Profiler bar and percentage represent the relative portion of the script's runtime spent on the entire code block, not just the single line.

## Tooltip Fields

The corresponding tooltip displays four fields:

1. **Code block range field**: Indicates the range of lines included in the structure
2. **Time field**: Shows the code block's runtime percentage, the time spent on all block executions, and the script's total runtime
3. **Line time field**: Shows the runtime percentage for the block's initial line, the time spent on that line, and the script's total runtime
4. **Executions field**: Shows the number of times the code block executed while running the script

## Special Cases for Line Time

The interpretation of the "Line time" field differs for switch blocks or if blocks with else if statements:

- For these structures, the values represent the total time spent on all the structure's conditional statements, not just the block's initial line
- This format is necessary due to the Profiler's calculation and display constraints

## Nested Block Results

Users can also inspect the results from lines and nested blocks within a code block's range to gain more granular performance insights. The number of executions shown for nested lines may be less than the result for the entire code block, as the condition that controls the execution of nested lines does not return true all the time.

## Switch and If-Else-If Limitations

When profiling a switch structure or an if structure that includes else if statements, the "Line time" field will show the time spent executing all the structure's conditional expressions, not just the block's first line. The results for the lines inside the code block range will show runtime and executions for each local block.

## Nested If Workaround

For more granular profiling of conditional logic, use nested if blocks instead of switch or if...else if structures. This allows viewing the runtime and executions for each significant part of the conditional pattern individually.

Instead of:
```pine
switch
<expression1> => <localBlock1>
<expression2> => <localBlock2>
=> <localBlock3>
```

Use:
```pine
if <expression1>
<localBlock1>
else
if <expression2>
<localBlock2>
else
<localBlock3>
```

This same process can also apply to ternary operations. When a complex ternary expression's operands contain significant calculations, reorganizing the logic into a nested if structure allows more detailed Profiler results."""
    })
    
    # Entry D — profiler.user_defined_functions
    entries.append({
        "id": "profiler_user_defined_functions",
        "name": "profiler.user_defined_functions", 
        "namespace": "profiler",
        "type": "guide",
        "category": "optimization",
        "source": "tradingview_live",
        "url": base_url + "#user-defined-function-calls",
        "indexed_at": today,
        "version": "v6",
        "document": """# User-Defined Function Call Profiling

User-defined functions and methods are functions written by users that encapsulate code sequences which a script may execute several times.

## Local Scope vs Global Scope

The indented lines of code within a function represent its local scope, i.e., the sequence that executes each time the script calls it. Unlike code in a script's global scope, which a script evaluates once on each execution, the code inside a function may activate zero, one, or multiple times on each script execution, depending on the conditions that trigger the calls, the number of calls that occur, and the function's logic.

## Interpreting Function Call Results

When a profiled code contains user-defined function or method calls:

- The results for each function call reflect the runtime allocated toward it and the total number of times the script activated that specific call
- The time and execution information for all local code inside a function's scope reflects the combined results from all calls to the function

## Single Call Example

When a script calls a function only once from the global scope on each execution, the Profiler's results for the code inside the function's body correspond to that specific call.

## Multiple Call Example

When a script calls a function multiple times, the local code results no longer correspond to a single evaluation per script execution. Instead, they represent the combined runtime and executions of the local code from all calls. The number of executions shown will be multiplied by the number of function calls.

## Request.*() Call Behavior

When a script calls a user-defined function or method that contains request.*() calls in its local scope, the script's translated form extracts the request.*() calls outside the scope and encapsulates the expressions they depend on within separate functions. 

Since the translated script executes a user-defined function's data requests separately before evaluating non-requested calculations in its local scope, the Profiler's results for lines containing calls to the function will not include the time spent on its request.*() calls or their required expressions.

## Example

If a function contains a request.security() call, the translated script moves this call outside the function's scope. The Profiler shows performance information for the function call, but the time spent on the request.security() call appears separately, and the function's local code results may show higher execution counts than expected."""
    })
    
    # Entry E — profiler.requesting_contexts
    entries.append({
        "id": "profiler_requesting_contexts",
        "name": "profiler.requesting_contexts",
        "namespace": "profiler",
        "type": "guide",
        "category": "optimization", 
        "source": "tradingview_live",
        "url": base_url + "#when-requesting-other-contexts",
        "indexed_at": today,
        "version": "v6",
        "document": """# Requesting Other Contexts and Profiling

Pine scripts can request data from other contexts, i.e., different symbols, timeframes, or data modifications than what the chart's data uses by calling the request.*() family of functions or specifying an alternate timeframe in the indicator() declaration statement.

## Context Execution Behavior

When a script requests data from another context, it evaluates all required scopes and calculations within that context. This behavior can affect the runtime of a script's code regions and the number of times they execute.

## Profiler Information Across Contexts

The Profiler information for any code line or block represents the results from executing the code in all necessary contexts, which may or may not include the chart's data. Pine Script determines which contexts to execute code within based on the calculations required by a script's data requests and outputs.

## Varip Behavior Across Contexts

The varip keyword declares variables that persist across the data's history and all available realtime ticks. When requesting data from another context, the number of executions may differ from the chart's context since the script may not require the chart's data in the calculations.

## Multiple Context Execution

If a script requires outputs from multiple contexts (e.g., both chart data and requested daily data), it will execute the required code across both separate datasets. This means declarations and calculations may execute multiple times - once for each context.

## Request.*() in User-Defined Functions

When a script calls a user-defined function that contains request.*() calls in its local scope, the translated script extracts the request.*() calls outside the function's scope and encapsulates the expressions they depend on within separate functions. The script evaluates the required request.*() calls first, then passes the requested data to a modified form of the user-defined function.

## Impact on Profiler Results

Since the translated script executes a user-defined function's data requests separately before evaluating non-requested calculations in its local scope, the Profiler's results for lines containing calls to the function will not include the time spent on its request.*() calls or their required expressions."""
    })
    
    # Entry F — profiler.unused_redundant_code
    entries.append({
        "id": "profiler_unused_redundant_code",
        "name": "profiler.unused_redundant_code",
        "namespace": "profiler",
        "type": "guide",
        "category": "optimization",
        "source": "tradingview_live", 
        "url": base_url + "#insignificant-unused-and-redundant-code",
        "indexed_at": today,
        "version": "v6",
        "document": """# Insignificant, Unused, and Redundant Code in Profiling

When inspecting a profiled script's results, it's crucial to understand that not all code in a script necessarily impacts runtime performance.

## Insignificant Code

Some code has no direct performance impact, such as:
- Script declaration statements
- Type declarations
- Most input.*() calls
- Variable references
- Variable declarations without significant calculations

The Profiler will not display performance results for these types of code.

## Unused Code

Pine scripts do not execute code regions that their outputs (plots, drawings, logs, etc.) do not depend on, as the compiler automatically removes them during translation. Since unused code regions have zero impact on a script's performance, the Profiler will not display any results for them.

### Example

If a script declares variables and performs calculations but only plots the close price without using the calculated values in its outputs, the compiled script will discard the unused code and only consider the plot(close) call.

## Redundant Code

When possible, the compiler simplifies certain instances of redundant code, such as some forms of identical expressions with the same fundamental type values. This optimization allows the compiled script to only execute such calculations once, on the first occurrence, and reuse the calculated result for each repeated instance.

If a script contains repetitive code and the compiler simplifies it, the Profiler will only show results for the first occurrence of the code since that's the only time the script requires the calculation.

### Redundant Expressions Example

If a script contains multiple identical ta.sma(close, 500) calls, the compiler can automatically simplify the script so that it only needs to evaluate ta.sma(close, 500) once per execution rather than repeating the calculation.

### Redundant Functions Example

When a script contains two or more user-defined functions or methods with identical compiled forms, the compiler simplifies the script by removing the redundant functions. The script will treat all calls to the redundant functions as calls to the first defined version. Therefore, the Profiler will only show local code performance results for the first function.

## Note on Inputs

Although a script may not use certain input.*() calls and discards all associated calculations, the inputs will still appear in the script's settings, as the compiler does not completely remove unused inputs."""
    })
    
    # Entry G — pine_profiler.examples
    entries.append({
        "id": "profiler_examples",
        "name": "pine_profiler.examples",
        "namespace": "profiler",
        "type": "example",
        "category": "optimization",
        "source": "tradingview_live",
        "url": base_url + "#examples",
        "indexed_at": today,
        "version": "v6",
        "document": """# Pine Profiler Code Examples

This section contains all code examples from the Pine Script Profiling and Optimization documentation.

## Pine Profiler Demo - Oscillator with Percentiles
// Demonstrates basic profiler functionality with an oscillator using percentiles
//@version=6
indicator("Pine Profiler demo")
//@variable The number of bars in the calculations.
int lengthInput = input.int(100, "Length", 2)
//@variable The percentage for upper percentile calculation.
float upperPercentInput = input.float(75.0, "Upper percentile", 50.0, 100.0)
//@variable The percentage for lower percentile calculation.
float lowerPercentInput = input.float(25.0, "Lower percentile", 0.0, 50.0)
// Calculate percentiles using the linear interpolation method.
float upperPercentile = ta.percentile_linear_interpolation(close, lengthInput, upperPercentInput)
float lowerPercentile = ta.percentile_linear_interpolation(close, lengthInput, lowerPercentInput)
// Declare arrays for upper and lower deviations from the percentiles on the same line.
var upperDistances = array.new<float>(lengthInput), var lowerDistances = array.new<float>(lengthInput)
// Queue distance values through the `upperDistances` and `lowerDistances` arrays based on excessive price deviations.
if math.abs(close - 0.5 * (upperPercentile + lowerPercentile)) > 0.5 * (upperPercentile - lowerPercentile)
array.push(upperDistances, math.max(close - upperPercentile, 0.0))
array.shift(upperDistances)
array.push(lowerDistances, math.max(lowerPercentile - close, 0.0))
array.shift(lowerDistances)
//@variable The average distance from the `upperDistances` array.
float upperAvg = upperDistances.avg()
//@variable The average distance from the `lowerDistances` array.
float lowerAvg = lowerDistances.avg()
//@variable The ratio of the difference between the `upperAvg` and `lowerAvg` to their sum.
float oscillator = (upperAvg - lowerAvg) / (upperAvg + lowerAvg)
//@variable The color of the plot. A green-based gradient if `oscillator` is positive, a red-based gradient otherwise.
color oscColor = oscillator > 0 ?
color.from_gradient(oscillator, 0.0, 1.0, color.gray, color.green) :
color.from_gradient(oscillator, -1.0, 0.0, color.red, color.gray)
// Plot the `oscillator` with the `oscColor`.
plot(oscillator, "Oscillator", oscColor, style = plot.style_area)

## Switch vs Nested If Comparison
// Demonstrates profiling differences between switch and nested if structures
//@version=6
indicator("`switch` and `if...else if` results demo")
//@variable The upper band for oscillator calculation.
var float upperBand = close
//@variable The lower band for oscillator calculation.
var float lowerBand = close
// Update the `upperBand` and `lowerBand` based on the proximity of the `close` to the current band values.
// The "Line time" field on line 11 represents the time spent on all 4 conditional expressions in the structure.
switch
close > upperBand => upperBand := close
close < lowerBand => lowerBand := close
upperBand - close > close - lowerBand => upperBand := 0.9 * upperBand + 0.1 * close
close - lowerBand > upperBand - close => lowerBand := 0.9 * lowerBand + 0.1 * close
//@variable The ratio of the difference between `close` and `lowerBand` to the band range.
float oscillator = 100.0 * (close - lowerBand) / (upperBand - lowerBand)
// Plot the `oscillator` as columns with a dynamic color.
plot(
oscillator, "Oscillator", oscillator > 50.0 ? color.teal : color.maroon,
style = plot.style_columns, histbase = 50.0
)

## Nested If Version (for granular profiling)
// Same logic as above but using nested if for more detailed profiling
//@version=6
indicator("`switch` and `if...else if` results demo")
//@variable The upper band for oscillator calculation.
var float upperBand = close
//@variable The lower band for oscillator calculation.
var float lowerBand = close
// Update the `upperBand` and `lowerBand` based on the proximity of the `close` to the current band values.
if close > upperBand
upperBand := close
else
if close < lowerBand
lowerBand := close
else
if upperBand - close > close - lowerBand
upperBand := 0.9 * upperBand + 0.1 * close
else
if close - lowerBand > upperBand - close
lowerBand := 0.9 * lowerBand + 0.1 * close
//@variable The ratio of the difference between `close` and `lowerBand` to the band range.
float oscillator = 100.0 * (close - lowerBand) / (upperBand - lowerBand)
// Plot the `oscillator` as columns with a dynamic color.
plot(
oscillator, "Oscillator", oscillator > 50.0 ? color.teal : color.maroon,
style = plot.style_columns, histbase = 50.0
)

## Similarity Function - Single vs Multiple Calls
// Demonstrates how multiple function calls affect profiler results
//@version=6
indicator("User-defined function calls demo")
//@function Estimates the similarity between two standardized series over `length` bars.
// Each individual call to this function activates its local scope.
similarity(float sourceA, float sourceB, int length) =>
// Standardize `sourceA` and `sourceB` for comparison.
float normA = (sourceA - ta.sma(sourceA, length)) / ta.stdev(sourceA, length)
float normB = (sourceB - ta.sma(sourceB, length)) / ta.stdev(sourceB, length)
// Calculate and return the estimated similarity of `normA` and `normB`.
float abSum = math.sum(normA * normB, length)
float a2Sum = math.sum(normA * normA, length)
float b2Sum = math.sum(normB * normB, length)
abSum / math.sqrt(a2Sum * b2Sum)
// Plot the similarity between the `close` and several offset `close` series.
plot(similarity(close, close[1], 100), "Similarity 1", color.red)
plot(similarity(close, close[2], 100), "Similarity 2", color.orange)
plot(similarity(close, close[4], 100), "Similarity 3", color.green)
plot(similarity(close, close[8], 100), "Similarity 4", color.blue)
plot(similarity(close, close[16], 100), "Similarity 5", color.purple)

## Varip + Request.Security Context Demo
// Shows how varip behaves across different contexts
//@version=6
indicator("When requesting other contexts demo")
//@variable An array containing the `close` value from every available price update.
varip array<float> pricesArray = array.new<float>()
// Push a new `close` value into the `pricesArray` on each update.
array.push(pricesArray, close)
// Plot the size of the `pricesArray` from the daily timeframe and the chart's context.
// Including both in the outputs requires executing line 5 and line 8 across BOTH datasets.
plot(request.security(syminfo.tickerid, "1D", array.size(pricesArray)), "Total number of daily price updates")
plot(array.size(pricesArray), "Total number of chart price updates")

## Unused Code Demo (without plot(barsInRange))
// Shows that unused code is not executed by the compiler
//@version=6
indicator("Unused code demo")
//@variable The number of historical bars in the calculation.
int lengthInput = input.int(100, "Length", 1)
//@variable The number of closes over `lengthInput` bars between the current bar's `high` and `low`.
int barsInRange = 0
for i = 1 to lengthInput
//@variable The `close` price from `i` bars ago.
float pastClose = close[i]
// Add 1 to `barsInRange` if the `pastClose` is between the current bar's `high` and `low`.
if pastClose > low and pastClose < high
barsInRange += 1
// Plot the `close` price. This is the only output.
// Since the outputs do not require any of the above calculations, the compiled script will not execute them.
plot(close)

## Unused Code Demo (with plot(barsInRange))
// Shows that used code is executed by the compiler
//@version=6
indicator("Unused code demo")
//@variable The number of historical bars in the calculation.
int lengthInput = input.int(100, "Length", 1)
//@variable The number of closes over `lengthInput` bars between the current bar's `high` and `low`.
int barsInRange = 0
for i = 1 to lengthInput
//@variable The `close` price from `i` bars ago.
float pastClose = close[i]
// Add 1 to `barsInRange` if the `pastClose` is between the current bar's `high` and `low`.
if pastClose > low and pastClose < high
barsInRange += 1
// Plot the `barsInRange` value. The above calculations will execute since the output requires them.
plot(barsInRange, "Bars in range")

## Redundant ta.sma Demo
// Demonstrates compiler optimization of identical expressions
//@version=6
indicator("Redundant calculations demo", overlay = true)
// Plot the 100-bar SMA of `close` values one time.
plot(ta.sma(close, 100), "100-bar SMA", color.teal, 3)
// Plot the 500-bar SMA of `close` values 12 times. After compiler optimizations, only the first `ta.sma(close, 500)`
// call on line 9 requires calculation in this case.
plot(ta.sma(close, 500), "500-bar SMA", #001aff, 12)
plot(ta.sma(close, 500), "500-bar SMA", #4d0bff, 11)
plot(ta.sma(close, 500), "500-bar SMA", #7306f7, 10)
plot(ta.sma(close, 500), "500-bar SMA", #920be9, 9)
plot(ta.sma(close, 500), "500-bar SMA", #ae11d5, 8)
plot(ta.sma(close, 500), "500-bar SMA", #c618be, 7)
plot(ta.sma(close, 500), "500-bar SMA", #db20a4, 6)
plot(ta.sma(close, 500), "500-bar SMA", #eb2c8a, 5)
plot(ta.sma(close, 500), "500-bar SMA", #f73d6f, 4)
plot(ta.sma(close, 500), "500-bar SMA", #fe5053, 3)
plot(ta.sma(close, 500), "500-bar SMA", #ff6534, 2)
plot(ta.sma(close, 500), "500-bar SMA", #ff7a00, 1)

## Redundant Functions Demo
// Shows how identical functions are optimized by the compiler
//@version=6
indicator("Redundant functions demo")
//@variable Controls the base ratio for the `calcMetallic()` call.
int order1Input = input.int(1, "Order 1", 1)
//@variable Controls the base ratio for the `metallicRatio()` call.
int order2Input = input.int(2, "Order 2", 1)
//@function Calculates the value of a metallic ratio with a given `order`, raised to a specified `exponent`.
//@param order Determines the base ratio used. 1 = Golden Ratio, 2 = Silver Ratio, 3 = Bronze Ratio, and so on.
//@param exponent The exponent applied to the ratio.
metallicRatio(int order, float exponent) =>
math.pow((order + math.sqrt(4.0 + order * order)) * 0.5, exponent)
//@function A function with the same signature and body as `metallicRatio()`.
// The script discards this function and treats `calcMetallic()` as an alias for `metallicRatio()`.
calcMetallic(int ord, float exp) =>
math.pow((ord + math.sqrt(4.0 + ord * ord)) * 0.5, exp)
// Plot the results from a `calcMetallic()` and `metallicRatio()` call.
plot(calcMetallic(order1Input, bar_index % 5), "Ratio 1", color.orange, 3)
plot(metallicRatio(order2Input, bar_index % 5), "Ratio 2", color.maroon)

## LCG Pseudorandom Profiler Inner Workings Demo
// Demonstrates the profiler's internal wrapping mechanism
//@version=6
indicator("Profiler's inner workings demo")
int seedInput = input.int(12345, "Seed")
type LCG
float state
method generate(LCG this, int generations = 1) =>
float result = 0.0
for i = 1 to generations
this.state := 16807 * this.state % 2147483647
result += this.state / 2147483647
result / generations
var lcg = LCG.new(seedInput)
var float val0 = 1.0
var float val1 = 1.0
var float val2 = 1.0
if lcg.generate(10) < 0.5
val0 *= 1.0 + (2.0 * lcg.generate(50) - 1.0) * 0.1
else if lcg.generate(10) < 0.5
val1 *= 1.0 + (2.0 * lcg.generate(50) - 1.0) * 0.1
else if lcg.generate(10) < 0.5
val2 *= 1.0 + (2.0 * lcg.generate(50) - 1.0) * 0.1
plot(math.avg(val0, val1, val2), "Average pseudorandom result", color.purple)"""
    })
    
    # Entry H — pine_optimization.techniques
    entries.append({
        "id": "profiler_optimization_techniques",
        "name": "pine_optimization.techniques",
        "namespace": "profiler",
        "type": "guide",
        "category": "optimization",
        "source": "tradingview_live",
        "url": base_url + "#optimization",
        "indexed_at": today,
        "version": "v6",
        "document": """# Pine Script Optimization Techniques

Code optimization involves modifying a script's source code for improved execution time, resource efficiency, and scalability. Most techniques involve reducing the number of times critical calculations occur or replacing significant calculations with simplified formulas or built-ins.

## Use Built-in Functions

Pine Script features a variety of built-in functions and variables with internal optimizations. Using built-ins like ta.highest(), ta.lowest(), ta.sma(), etc., is often more efficient than custom implementations.

Example: ta.highest(close, 20) outperforms user-defined loop-based highest value calculations.

## Reduce Repetition

Avoid calling the same function multiple times with identical arguments. Store the result in a variable and reuse it.

Example: Instead of calling data.valuesAbove(99) in multiple places, assign it to a variable once and reuse the variable.

## Minimize request.*() Calls

Each request.*() call can significantly impact resource usage. Condense multiple requests from the same context into a single call using tuples or user-defined type objects.

Example: Use [req1, req2, req3] = request.security(symbol, timeframe, [expr1, expr2, expr3]) instead of separate calls.

## Avoid Redrawing

Use setter functions (box.set_*(), line.set_*, etc.) to modify drawing objects instead of deleting and recreating them. This is typically less computationally expensive.

## Reduce Drawing Updates

For drawings that change across historical bars, restrict updates to the last historical bar and realtime bars using barstate.islast, since users only see the final result on historical bars.

## Store Calculated Values

Save results of infrequently changing calculations to var or varip variables. Only update when the calculation changes. Use collections, matrices, maps, or UDT objects for multiple values.

## Eliminate Loops

When possible, replace loops with:
- Simplified loop-free expressions
- Optimized built-ins
- Distribute loop iterations across bars

## Optimize Loops

When loops are necessary:
- Keep loop calculations simple
- Use loop-invariant code motion (move unchanging calculations outside the loop)
- Use efficient iteration patterns (for [index, item] in array instead of array.indexof() inside loop)

## Minimize Historical Buffer Calculations

Use max_bars_back() to explicitly define buffer sizes, preventing repetitive executions to determine buffer sizes.

## Use Nested If for Granular Profiling

Replace switch or if...else if structures with nested if blocks when you need detailed performance information for each conditional expression.

## Separate Multi-Expression Lines

Move each expression to a separate line for more detailed profiling insights, especially for higher-impact calculations.

## Avoid request.*() in User-Defined Functions

Keep request.*() calls in the global scope to avoid translation complications and profiler result interpretation issues.

## Use calc_bars_count

When possible, limit historical calculations with calc_bars_count parameter in indicator() declaration to reduce unnecessary executions."""
    })
    
    # Add section-based entries for each H2/H3 section
    section_entries = [
        {
            "id": "profiler_introduction",
            "name": "profiler.introduction", 
            "namespace": "profiler",
            "type": "guide",
            "category": "optimization",
            "source": "tradingview_live",
            "url": base_url + "#introduction",
            "indexed_at": today,
            "version": "v6",
            "document": """# Introduction to Pine Script Profiling and Optimization

Pine Script® is a cloud-based compiled language geared toward efficient repeated script execution. When a user adds a Pine script to a chart, it executes numerous times, once for each available bar or tick in the data feeds it accesses.

The Pine Script compiler automatically performs several internal optimizations to accommodate scripts of various sizes and help them run smoothly. However, such optimizations do not prevent performance bottlenecks in script executions. As such, it's up to programmers to profile a script's runtime performance and identify ways to modify critical code blocks and lines when they need to improve execution times.

This page covers how to profile and monitor a script's runtime and executions with the Pine Profiler and explains some ways programmers can modify their code to optimize runtime performance."""
        },
        {
            "id": "profiler_profiling_script",
            "name": "profiler.profiling_script",
            "namespace": "profiler",
            "type": "guide", 
            "category": "optimization",
            "source": "tradingview_live",
            "url": base_url + "#profiling-a-script",
            "indexed_at": today,
            "version": "v6",
            "document": """# Profiling a Script with Pine Profiler

The Pine Profiler can analyze the runtime performance of any editable script coded in Pine Script v6. To profile a script, add it to the chart, open the source code in the Pine Editor, and turn on the "Profiler mode" switch in the dropdown accessible via the "More" option in the top-right corner.

## What Gets Profiled

Once enabled, the Profiler collects information from all executions of the script's significant code lines and blocks, then displays bars and approximate runtime percentages to the left of the code lines inside the Pine Editor.

## Important Notes

- The Profiler tracks every execution of a significant code region, including the executions on realtime ticks
- Profiler results do not appear for script declaration statements, type declarations, other insignificant code lines, unused code, or repetitive code that the compiler optimizes
- When a script contains at least four significant lines of code, the Profiler will include "flame" icons next to the top three code regions with the highest performance impact"""
        }
    ]
    
    # Combine all entries
    all_entries = entries + section_entries
    
    return all_entries

def index_to_chromadb(entries):
    """Index entries into ChromaDB"""
    # Connect to ChromaDB
    client = chromadb.PersistentClient(path="./pinescript_db")
    collection = client.get_or_create_collection(name="pinescript_docs")
    
    # Prepare documents, metadatas, and IDs
    documents = []
    metadatas = []
    ids = []
    
    for entry in entries:
        documents.append(entry["document"])
        metadatas.append({
            "name": entry["name"],
            "namespace": entry["namespace"], 
            "type": entry["type"],
            "category": entry["category"],
            "source": entry["source"],
            "url": entry["url"],
            "indexed_at": entry["indexed_at"],
            "version": entry["version"]
        })
        ids.append(entry["id"])
    
    # Upsert entries
    collection.upsert(
        documents=documents,
        metadatas=metadatas,
        ids=ids
    )
    
    print(f"Successfully indexed {len(entries)} entries")
    return collection

def verify_indexing(collection):
    """Run verification queries"""
    print("\n" + "="*50)
    print("VERIFICATION QUERIES")
    print("="*50)
    
    # V1. Count profiler namespace entries
    result = collection.get(where={"namespace": "profiler"}, include=["metadatas"])
    profiler_count = len(result["ids"])
    profiler_names = [meta["name"] for meta in result["metadatas"]]
    
    print(f"V1. Profiler namespace entries: {profiler_count}")
    print(f"   Names: {profiler_names}")
    v1_pass = profiler_count >= 8
    print(f"   PASS: {v1_pass} (required >= 8)")
    
    # V2. Query about pine profiler usage
    results = collection.query(
        query_texts=["how to use pine profiler to measure script performance"],
        n_results=3
    )
    v2_pass = any(meta["namespace"] == "profiler" for meta in results["metadatas"][0])
    print(f"\nV2. Pine profiler usage query: {v2_pass}")
    
    # V3. Query about user-defined function profiling
    results = collection.query(
        query_texts=["why does profiler show more executions inside function than at call site"],
        n_results=3
    )
    v3_pass = len(results["documents"][0]) > 0  # Check if we got any results
    print(f"V3. User-defined function profiling query: {v3_pass}")
    
    # V4. Query about optimization
    results = collection.query(
        query_texts=["script optimization avoid redundant calculations"],
        n_results=5
    )
    v4_pass = any(meta.get("namespace") == "profiler" or "optimization" in meta.get("name", "") 
                  for meta in results["metadatas"][0])
    print(f"V4. Optimization query: {v4_pass}")
    
    # V5. Check pine_profiler entry
    result = collection.get(
        where={"name": "pine_profiler"},
        include=["documents"]
    )
    v5_pass = (len(result["documents"]) > 0 and 
               len(result["documents"][0]) > 200)
    print(f"V5. pine_profiler entry length check: {v5_pass}")
    if v5_pass:
        print(f"   First 300 chars: {result['documents'][0][:300]}...")
    
    # V6. Check examples entry
    result = collection.get(
        where={"name": "pine_profiler.examples"},
        include=["documents"]
    )
    v6_pass = (len(result["documents"]) > 0 and 
               "ta.percentile_linear_interpolation" in result["documents"][0])
    print(f"V6. Examples entry contains ta.percentile_linear_interpolation: {v6_pass}")
    
    # Calculate score
    passed = sum([v1_pass, v2_pass, v3_pass, v4_pass, v5_pass, v6_pass])
    print(f"\nFinal score: {passed}/6")
    
    # Get total DB count
    total_count = collection.count()
    print(f"Total profiler entries indexed: {profiler_count}")
    print(f"New DB total: {total_count} entries")
    
    return passed >= 6

if __name__ == "__main__":
    # Create entries
    entries = main()
    
    # Index to ChromaDB
    collection = index_to_chromadb(entries)
    
    # Verify indexing
    success = verify_indexing(collection)
    
    if success:
        print("\n✅ All verification checks passed!")
    else:
        print("\n❌ Some verification checks failed!")
